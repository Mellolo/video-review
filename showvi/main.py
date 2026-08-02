"""
Main entry point for the Self-Evolving Video Director Agent.

Usage:
    python main.py --storyboard storyboards/storyboard.json
    python main.py --storyboard storyboards/storyboard.json --output ./output --model gemini-3-pro-preview
    python main.py --resume latest
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import AppConfig
from models import ProjectState, VideoScript, WorkUnit
from agent import VideoDirectorAgent
from tools import build_tool_registry
from pipeline import load_skills, SkillPipelineExecutor
from planner import Planner
from tools.storyboard_parser import load_storyboard, Storyboard
from tools.batch_transition import generate_transition_units, interleave_with_transitions
from utils.io import load_json
from utils.logger import setup_run_logging

RUNTIME_OVERRIDES_FILE_NAME = ".runtime_overrides.json"

os.environ.setdefault("SEEDDANCE_BACKEND", "jimeng")
os.environ.setdefault("IMAGE_PROVIDER", "google")
os.environ.setdefault("LLM_PROVIDER", "openai_compatible")
os.environ.setdefault("GEMINI_BACKEND", "openai_compatible")  # backward compat
os.environ.setdefault("LLM_MODEL", "gemini-3-flash-preview")
os.environ.setdefault("GEMINI_MODEL", "gemini-3-flash-preview")  # backward compat



def _load_runtime_overrides_for_run(run_dir: str | Path | None) -> dict:
    if not run_dir:
        return {}
    path = Path(run_dir) / RUNTIME_OVERRIDES_FILE_NAME
    if not path.exists():
        return {}
    data = load_json(path, default={})
    if isinstance(data, dict) and isinstance(data.get("overrides"), dict):
        return data.get("overrides", {})
    return data if isinstance(data, dict) else {}


def load_script_from_storyboard(
    storyboard_path: str,
) -> tuple[VideoScript, Storyboard]:
    """
    Load a storyboard and convert to VideoScript with WorkUnits.

    Each scene in the storyboard maps 1:1 to a WorkUnit.
    The scene's seedance_prompt (stored as visual_description) is used directly.
    """
    try:
        storyboard = load_storyboard(storyboard_path)

        work_units = []
        for i, sb_scene in enumerate(storyboard.scenes, 1):
            prompt = sb_scene.visual_description or sb_scene.sora_prompt or ""
            unit = WorkUnit(
                unit_id=i,
                prompt=prompt,
                original_prompt=prompt,
                duration_seconds=sb_scene.parse_duration_to_seconds(),
                scene_numbers=[sb_scene.scene_number],
                visual_description=sb_scene.visual_description,
                plot_description=sb_scene.plot_description,
                characters_in_scene=sb_scene.characters_in_scene,
                camera_angle=sb_scene.camera_angle,
                mood=sb_scene.mood,
                lighting=sb_scene.lighting,
            )
            work_units.append(unit)

        script = VideoScript(
            title=storyboard.video_analysis.get("theme", "Untitled"),
            description=storyboard.video_analysis.get("style", ""),
            work_units=work_units,
        )
        return script, storyboard

    except FileNotFoundError:
        print(f"Error: Storyboard not found: {storyboard_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading storyboard: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)


def save_project_report(state: ProjectState, output_path: str):
    report = {
        "project_id": state.project_id,
        "statistics": state.get_status_report(),
        "units": [],
    }
    for unit in state.script.work_units:
        unit_report = {
            "unit_id": unit.unit_id,
            "scene_numbers": unit.scene_numbers,
            "prompt": unit.prompt[:200],
            "is_completed": unit.is_completed,
            "final_video_path": unit.final_video_path,
            "escalation_level": unit.escalation_level,
            "total_attempts": len(unit.attempts),
        }
        report["units"].append(unit_report)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[REPORT] Saved: {output_path}")


def _find_source_storyboard(run_local_path: str, output_dir: str) -> Optional[Path]:
    """Infer the source storyboard path from the run-local copy.

    Run-local:  .../output/{sb_name}/{timestamp}/{sb_name}.json
    Source:     .../storyboards/{sb_name}.json

    We walk up from output_dir to find the 'storyboards' sibling directory.
    """
    run_local = Path(run_local_path)
    out_dir = Path(output_dir)

    # output_dir is like: data/users/{uid}/output/{sb_name}/{ts}
    # We need:            data/users/{uid}/storyboards/{sb_name}.json
    try:
        # Go up from output_dir: {ts} → {sb_name} → output → {user_dir}
        user_dir = out_dir.parent.parent.parent
        storyboards_dir = user_dir / "storyboards"
        if storyboards_dir.is_dir():
            source = storyboards_dir / run_local.name
            if source.exists() and source != run_local.resolve():
                return source
    except Exception:
        pass
    return None


def _sync_storyboard_descriptions_from_source(
    run_local_path: str,
    output_dir: str,
    storyboard: "Storyboard",
) -> None:
    """Sync entity descriptions from the source storyboard into the run-local copy.

    When the user edits character/location/prop descriptions in the dashboard,
    those changes go to the source storyboard.  On resume, we need to pull
    those updates into the run-local snapshot and the in-memory storyboard.
    """
    source_path = _find_source_storyboard(run_local_path, output_dir)
    if not source_path:
        return

    try:
        with open(source_path, "r", encoding="utf-8") as f:
            source_sb = json.load(f)
    except Exception:
        return

    updated = 0

    # Build lookup from source: name → entity dict
    for category, attr_name in [
        ("characters", "characters"),
        ("locations", "locations"),
        ("props", "props"),
    ]:
        source_entities = {e["name"]: e for e in source_sb.get(category, []) if e.get("name")}
        local_entities = getattr(storyboard, attr_name, {}) or {}

        for name, local_obj in local_entities.items():
            source_entity = source_entities.get(name)
            if not source_entity:
                continue

            # Sync description
            src_desc = source_entity.get("description", "")
            if src_desc and src_desc != getattr(local_obj, "description", ""):
                old_desc = getattr(local_obj, "description", "")[:40]
                local_obj.description = src_desc
                updated += 1
                print(f"  ↻ Synced {name} description from source: {old_desc}... → {src_desc[:40]}...")

            # Sync personality (characters only)
            src_personality = source_entity.get("personality", "")
            if src_personality and src_personality != getattr(local_obj, "personality", ""):
                local_obj.personality = src_personality

    if updated:
        # Also update the run-local JSON file on disk
        try:
            with open(run_local_path, "r", encoding="utf-8") as f:
                run_sb_data = json.load(f)

            for category in ("characters", "locations", "props"):
                source_entities = {e["name"]: e for e in source_sb.get(category, []) if e.get("name")}
                for entity in run_sb_data.get(category, []):
                    src = source_entities.get(entity.get("name"))
                    if not src:
                        continue
                    if src.get("description"):
                        entity["description"] = src["description"]
                    if src.get("personality"):
                        entity["personality"] = src["personality"]

            with open(run_local_path, "w", encoding="utf-8") as f:
                json.dump(run_sb_data, f, ensure_ascii=False, indent=2)
            print(f"  Updated {updated} description(s) from source storyboard")
        except Exception as e:
            print(f"  Warning: failed to write updated descriptions to run-local storyboard: {e}")


def _copy_storyboard_into_run(source_path: str, output_dir: Path) -> Optional[Path]:
    """Persist a storyboard snapshot inside the run directory for future history browsing."""
    src = Path(source_path).resolve()
    if not src.exists() or not src.is_file():
        return None

    dest = output_dir / src.name
    if src == dest:
        return dest

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def _recover_pending_seeddance(project_state: ProjectState,
                               runtime_overrides: dict | None = None) -> int:
    """Check for in-progress attempts with SeedDance history_ids and try to recover.

    For each in-progress attempt that has a saved history_id (persisted via the
    on_task_submitted callback before the process crashed), we query the backend
    to see if the video is ready and download it if so.

    Returns the number of successfully recovered units.
    """
    recovered = 0
    jimeng_client = None

    # Try to get session_id from runtime_overrides (proxy mode) or env
    override_session_id = (runtime_overrides or {}).get("seeddance_session_id")

    for unit in project_state.script.work_units:
        if unit.is_completed:
            continue
        for attempt in unit.attempts:
            if attempt.status != "in_progress":
                continue
            history_id = attempt.metadata.get("history_id") if attempt.metadata else None
            if not history_id:
                attempt.status = "failed"
                attempt.error_message = "Process interrupted (no history_id)"
                continue

            backend = (attempt.metadata.get("backend") or "jimeng").lower()
            print(f"  Recovering Unit {unit.unit_id} attempt {attempt.attempt_id} "
                  f"(history_id={history_id}, backend={backend})...")

            try:
                if jimeng_client is None:
                    from clients.seeddance import SeeddanceClient
                    jimeng_client = SeeddanceClient(
                        session_id=override_session_id or None)
                recovered += _recover_jimeng_attempt(
                    attempt, unit, project_state, jimeng_client)
            except ImportError as e:
                print(f"  Warning: Cannot import client for {backend}: {e}")
                attempt.status = "failed"
                attempt.error_message = f"Client import failed: {e}"
            except Exception as e:
                print(f"  ✗ Recovery check failed for Unit {unit.unit_id}: {e}")
                attempt.status = "failed"
                attempt.error_message = f"Recovery failed: {e}"

    if recovered:
        project_state.save_checkpoint()
    return recovered


def _recover_jimeng_attempt(attempt, unit, project_state, client) -> int:
    """Try to recover a single attempt from Jimeng (SeeddanceClient). Returns 0 or 1."""
    history_id = attempt.metadata["history_id"]
    progress = client.check_progress(history_id)

    # check_progress returns "processing" or "generating" for in-flight tasks
    if progress["status"] in ("generating", "processing"):
        print(f"  ⏳ Unit {unit.unit_id} still generating on Jimeng, waiting...")
        try:
            result = client.wait_for_video(history_id)
            if result and result.get("url"):
                progress = {"status": "done", "video_url": result["url"]}
            else:
                attempt.status = "failed"
                attempt.error_message = "wait_for_video returned no URL"
                print(f"  ✗ Unit {unit.unit_id}: polling finished but no video URL")
                return 0
        except Exception as e:
            attempt.status = "failed"
            attempt.error_message = f"Recovery polling failed: {e}"
            print(f"  ✗ Unit {unit.unit_id}: polling failed: {e}")
            return 0

    if progress["status"] == "done" and progress.get("video_url"):
        output_dir = project_state.output_directory
        out_path = Path(output_dir) / f"segment_{unit.unit_id:03d}_recovered.mp4"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if client.download_video_file(progress["video_url"], str(out_path)):
            attempt.status = "success"
            attempt.output_path = str(out_path)
            unit.final_video_path = str(out_path)
            unit.is_completed = True
            print(f"  ✓ Recovered Unit {unit.unit_id} → {out_path}")
            return 1
        else:
            attempt.status = "failed"
            attempt.error_message = "Recovery download failed"
            print(f"  ✗ Download failed for Unit {unit.unit_id}")
    else:
        attempt.status = "failed"
        attempt.error_message = (
            f"SeedDance task {progress['status']}: "
            f"{progress.get('error', 'unknown')}")
        print(f"  ✗ Unit {unit.unit_id}: {attempt.error_message}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Self-Evolving Video Director Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--storyboard", type=str, default="storyboards/book2_storyboard.json")
    parser.add_argument("--output", type=str, default="./output")
    parser.add_argument("--model", type=str, default="gemini-3-pro-preview")
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--no-escalate", action="store_true")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--resume", type=str, metavar="CHECKPOINT")
    parser.add_argument("--debug", action="store_true", help="Step-by-step debug mode (interactive)")
    parser.add_argument("--no-select", action="store_true",
                        help="Skip post-generation Gemini video selection")
    parser.add_argument("--batch-mode", action="store_true",
                        help="Batch creation mode: generate original groups + transition "
                             "videos between groups, skip final editing (raw material output)")
    parser.add_argument("--transition-bridge", action="store_true",
                        help="Enable scene transition bridging: extract last frame from "
                             "previous segment, summarise prior plot, and generate a "
                             "transition-aware prompt for seamless scene-to-scene continuity")
    args = parser.parse_args()

    # Build config
    cfg = AppConfig.from_env()
    cfg.llm_model = args.model
    cfg.max_attempts_per_scene = args.max_attempts
    cfg.auto_escalate = not args.no_escalate
    cfg.parallel_mode = args.parallel
    cfg.max_parallel_tasks = args.max_parallel
    cfg.output_dir = args.output
    cfg.runtime_overrides = {}
    cfg.enable_video_selection = not args.no_select and not args.batch_mode
    cfg.batch_mode = getattr(args, "batch_mode", False)
    cfg.enable_transition_bridge = getattr(args, "transition_bridge", False)

    # Check API keys
    if not cfg.gemini_api_key:
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)
    # Video generator keys are checked dynamically by Skill availability
    if not cfg.seeddance_session_id:
        print("Warning: SEEDDANCE_SESSION_ID not set — no video skills available")

    # Handle resume
    if args.resume:
        cp = args.resume
        if cp.lower() == "latest":
            sb_name = Path(args.storyboard).stem if args.storyboard else None
            cp = ProjectState.find_latest_checkpoint(args.output, storyboard_name=sb_name)
            if not cp:
                hint = f" for storyboard '{sb_name}'" if sb_name else ""
                print(f"Error: No checkpoint found{hint}")
                sys.exit(1)
        project_state = ProjectState.load_checkpoint(cp)
        storyboard = None
        if project_state.storyboard_path and Path(project_state.storyboard_path).exists():
            storyboard = load_storyboard(project_state.storyboard_path)
            print(f"Storyboard reloaded: {project_state.storyboard_path}")

            # ── Sync descriptions from source storyboard ────────────
            # The run-local copy is a snapshot from when the run started.
            # If the user edited descriptions in the dashboard, those edits
            # go to the source storyboard.  Sync them into the run-local copy.
            _sync_storyboard_descriptions_from_source(
                project_state.storyboard_path,
                project_state.output_directory,
                storyboard,
            )

            # Restore generated charsheet paths onto storyboard objects
            restored = 0
            for key, img_path in project_state.generated_charsheets.items():
                if not Path(img_path).exists():
                    print(f"  Warning: charsheet missing on disk: {img_path}")
                    continue
                kind, name = key.split(":", 1)
                if kind == "char" and name in storyboard.characters:
                    storyboard.characters[name].image_path = img_path
                    restored += 1
                elif kind == "loc" and name in storyboard.locations:
                    storyboard.locations[name].image_path = img_path
                    restored += 1
                elif kind == "prop" and name in storyboard.props:
                    storyboard.props[name].image_path = img_path
                    restored += 1
            if restored:
                print(f"  Restored {restored} charsheet/locsheet/propsheet path(s) from checkpoint")
        else:
            print("Warning: storyboard snapshot missing in run and source storyboard unavailable — charsheets and context may be incomplete")
        setup_run_logging(project_state.output_directory)
        cfg.runtime_overrides = _load_runtime_overrides_for_run(project_state.output_directory)

        # Reset units that were marked completed but have no usable video
        reset_count = 0
        for unit in project_state.script.work_units:
            needs_reset = False
            if unit.is_completed and not unit.final_video_path:
                needs_reset = True
            elif unit.is_completed and unit.final_video_path:
                if not Path(unit.final_video_path).exists():
                    print(f"  Warning: Unit {unit.unit_id} video missing: "
                          f"{unit.final_video_path}")
                    needs_reset = True
            if needs_reset:
                unit.is_completed = False
                unit.final_video_path = None
                reset_count += 1
        project_state.completed_at = None

        # Try to recover in-flight SeedDance tasks from previous run
        recovered = _recover_pending_seeddance(project_state, cfg.runtime_overrides)

        status = project_state.get_status_report()
        print(f"Resumed from: {cp}")
        print(f"  Units: {status['completed_units']}/{status['total_units']} completed"
              + (f", {reset_count} reset for retry" if reset_count else "")
              + (f", {recovered} recovered from SeedDance" if recovered else ""))
    else:
        # Load storyboard
        print(f"Loading storyboard: {args.storyboard}")
        script, storyboard = load_script_from_storyboard(
            args.storyboard,
        )
        print(f"Title: {script.title}")
        print(f"Work units: {len(script.work_units)}")

        # ── Batch mode: generate transition units ──────────────────
        if args.batch_mode and storyboard and len(script.work_units) >= 2:
            print("\n[BATCH MODE] Generating transition units between groups...")
            transition_units = generate_transition_units(
                work_units=script.work_units,
                storyboard=storyboard,
                model="gemini-3-flash-preview",
            )
            if transition_units:
                all_units = interleave_with_transitions(
                    script.work_units, transition_units,
                )
                script.work_units = all_units
                n_orig = len(all_units) - len(transition_units)
                print(f"[BATCH MODE] {n_orig} original + "
                      f"{len(transition_units)} transition = "
                      f"{len(all_units)} total work units")
                for u in transition_units:
                    print(f"  T{u.unit_id} [{u.group_name}]: "
                          f"scenes {u.scene_numbers}, {u.duration_seconds:.1f}s")
            else:
                print("[BATCH MODE] No transition units generated")
            print("[BATCH MODE] Video selection DISABLED (raw material output)\n")

        # Create output directory: output/<storyboard_name>/<timestamp>
        storyboard_name = Path(args.storyboard).stem
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output) / storyboard_name / ts
        output_dir.mkdir(parents=True, exist_ok=True)
        setup_run_logging(str(output_dir))
        cfg.runtime_overrides = _load_runtime_overrides_for_run(output_dir)

        storyboard_snapshot = _copy_storyboard_into_run(args.storyboard, output_dir)
        effective_storyboard_path = storyboard_snapshot or Path(args.storyboard).resolve()
        if storyboard_snapshot:
            storyboard.filepath = storyboard_snapshot
            print(f"Storyboard snapshot saved: {storyboard_snapshot}")

        project_state = ProjectState(
            project_id=f"project_{ts}",
            script=script,
            output_directory=str(output_dir),
            storyboard_path=str(effective_storyboard_path),
        )

    # ── Dependency injection: assemble everything ──────────────────
    tools = build_tool_registry()
    skills = load_skills("skills")
    planner = Planner(skills=skills, tools=tools)
    pipeline_executor = SkillPipelineExecutor(tools=tools, debug=args.debug)

    if args.debug:
        cfg.parallel_mode = False
        print("[DEBUG] Debug mode ON — forced sequential execution")

    agent = VideoDirectorAgent(
        project_state=project_state,
        tools=tools,
        planner=planner,
        pipeline_executor=pipeline_executor,
        config=cfg,
        storyboard=storyboard,
    )

    try:
        final_state = agent.run()

        report_path = Path(final_state.output_directory) / f"{final_state.project_id}_report.json"
        save_project_report(final_state, str(report_path))

        # Print summary
        status = final_state.get_status_report()
        print("\n" + "=" * 70)
        print("FINAL SUMMARY")
        print("=" * 70)
        print(f"Completion: {status['completion_percentage']:.1f}%")
        print(f"Units: {status['completed_units']}/{status['total_units']}")
        print(f"Attempts: {status['total_attempts']}")
        print(f"Time: {status['elapsed_time']:.1f}s")

        for unit in final_state.script.work_units:
            if unit.final_video_path:
                print(f"  Unit {unit.unit_id}: {unit.final_video_path}")

        if final_state.script.is_complete():
            print("\nAll units completed successfully!")
        else:
            print("\nSome units could not be completed.")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Install API usage tracking hooks (safe no-op if dashboard DB unavailable)
    try:
        from dashboard.usage_tracker import install_hooks, usage_context
        install_hooks()
        _owner_uid = int(os.environ.get("VIDEO_AGENT_OWNER_USER_ID", "0"))
        _usage_cm = usage_context(user_id=_owner_uid, step="video_gen")
        _usage_cm.__enter__()
    except Exception:
        pass
    main()
