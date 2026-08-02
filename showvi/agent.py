"""
Autonomous Video Director Agent.

Two execution modes:
  - Sequential (Skill-driven: Plan → Execute Pipeline → Evaluate → Rewrite)
  - Parallel   (N independent workers; each handles the full lifecycle
                — generate → critique → rewrite → retry — then picks up
                the next unit from the queue.  No synchronisation barrier.)
"""

import json
import logging
import os
import queue
import re
import signal
import time
import threading
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Global stop event — set by SIGTERM handler so worker threads can exit early
_STOP_EVENT = threading.Event()


def _install_sigterm_handler():
    """Set _STOP_EVENT on SIGTERM so workers skip pending submissions."""
    def _handler(signum, frame):
        _STOP_EVENT.set()
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (OSError, ValueError):
        pass  # Not in main thread or signal not available

from config import AppConfig
from models import (
    ProjectState,
    WorkUnit,
    GenerationAttempt,
    AttemptStatus,
)
from tools.base import BaseTool, ExecutionContext, ToolResult
from tools.regen_queue import load_requests, save_requests
from pipeline import SkillDefinition, SkillPipelineExecutor
from planner import Planner


class VideoDirectorAgent:
    """Orchestrates video generation with self-healing capabilities."""

    def __init__(
        self,
        project_state: ProjectState,
        tools: Dict[str, BaseTool],
        planner: Planner,
        pipeline_executor: SkillPipelineExecutor,
        config: AppConfig,
        storyboard=None,
    ):
        self.project_state = project_state
        self.tools = tools
        self.planner = planner
        self.pipeline_executor = pipeline_executor
        self.config = config
        self.storyboard = storyboard
        self._log = logging.getLogger("video_agent.agent")
        self._checkpoint_lock = threading.Lock()
        self._external_regen_lock = threading.Lock()
        self._run_dir = Path(project_state.output_directory)

        # Pass config to ImageGen tool if it exists
        if "image_gen" in tools:
            tools["image_gen"].config = config

        self._log.info(
            "=" * 60 + "\nAgent initialized\n"
            "  model              : %s\n"
            "  max_attempts       : %d\n"
            "  parallel_mode      : %s\n"
            "  registered tools   : %s\n"
            "  available skills   : %s",
            config.llm_model,
            config.max_attempts_per_scene,
            config.parallel_mode,
            list(tools.keys()),
            list(planner.skills.keys()),
        )
        print(f"[AGENT] Initialized with model: {config.llm_model}")
        print(f"[AGENT] Registered tools: {', '.join(tools.keys())}")
        print(f"[AGENT] Available skills: {', '.join(planner.skills.keys())}")
        print(f"[AGENT] Parallel mode: {'ENABLED' if config.parallel_mode else 'DISABLED'}")

    # ── Main entry points ─────────────────────────────────────────────

    def run(self) -> ProjectState:
        if self.config.parallel_mode:
            if getattr(self.config, "enable_transition_bridge", False):
                self._log.warning(
                    "Transition bridge requires sequential execution — "
                    "overriding parallel mode")
                print("[AGENT] Transition bridge enabled — forcing sequential mode")
            else:
                return self._run_parallel()
        return self._run_sequential()

    # ══════════════════════════════════════════════════════════════════
    #  SEQUENTIAL MODE
    # ══════════════════════════════════════════════════════════════════

    def _run_sequential(self) -> ProjectState:
        print("\n" + "=" * 70)
        print("VIDEO DIRECTOR AGENT - Starting Production")
        print("=" * 70 + "\n")
        start = time.time()

        while True:
            priority_unit_ids = self._consume_external_regen_requests()
            unit = None
            for unit_id in priority_unit_ids:
                candidate = self._find_unit_by_id(unit_id)
                if candidate and not candidate.is_completed:
                    unit = candidate
                    break

            if unit is None:
                if self.project_state.script.is_complete():
                    break
                unit = self.project_state.script.get_next_incomplete()
                if unit is None:
                    time.sleep(0.5)
                    continue

            print(f"\n{'─' * 70}")
            print(f"Processing Unit {unit.unit_id}/{len(self.project_state.script.work_units)}")
            print(f"{'─' * 70}")
            print(f"Scenes: {unit.scene_numbers}")
            print(f"Duration: {unit.duration_seconds:.1f}s")
            print()

            success = self._process_unit(unit)
            if success:
                unit.is_completed = True
                unit.abandoned_no_video = False
                print(f"\n[AGENT] Unit {unit.unit_id} completed successfully!")
            else:
                unit.abandoned_no_video = True
                unit.is_completed = True
                print(f"\n[AGENT] Unit {unit.unit_id} failed after {len(unit.attempts)} attempts (no video)")

        self._select_best_videos()
        self.project_state.mark_complete()
        self._save_checkpoint("production complete")
        self._print_summary(time.time() - start)
        return self.project_state

    def _get_remaining_attempt_budget(self, unit: WorkUnit) -> int:
        extra_remaining = max(0, unit.pending_extra_attempts)
        if extra_remaining > 0:
            return extra_remaining
        base_remaining = max(0, self.config.max_attempts_per_scene - len(unit.attempts))
        return base_remaining

    def _process_unit(self, unit: WorkUnit) -> bool:
        """Plan → Execute Pipeline → Evaluate → Rewrite, repeat."""
        self._log.info(
            "─" * 60 + "\nProcessing Unit %s  scenes=%s  duration=%.1fs",
            unit.unit_id, unit.scene_numbers, unit.duration_seconds,
        )

        excluded_skills: List[str] = []
        current_skill: Optional[SkillDefinition] = None
        remaining_attempts = self._get_remaining_attempt_budget(unit)

        if remaining_attempts <= 0:
            self._log.info("Unit %s has no remaining attempt budget", unit.unit_id)
            return bool(unit.final_video_path)

        attempt_index = 0
        while attempt_index < remaining_attempts:
            attempt_index += 1
            latest_attempt = unit.get_latest_attempt()
            if latest_attempt and latest_attempt.status == AttemptStatus.IN_PROGRESS and not latest_attempt.metadata.get("history_id") and not latest_attempt.output_path:
                attempt_num = latest_attempt.attempt_id
                attempt = latest_attempt
                attempt.tool_used = attempt.tool_used or "skill:pending"
                attempt.metadata.pop("placeholder", None)
                if attempt.max_attempts_hint is None:
                    attempt.max_attempts_hint = remaining_attempts
                self._log.info("── Unit %s · Reusing placeholder attempt %d (%d/%d in current run) ──",
                               unit.unit_id, attempt_num, attempt_index, remaining_attempts)
                print(f"\n[AGENT] ──── Attempt {attempt_num} ({attempt_index}/{remaining_attempts}) ────")
            else:
                attempt_num = unit.get_next_attempt_id()
                attempt = None
                self._log.info("── Unit %s · Attempt %d (%d/%d in current run) ──",
                               unit.unit_id, attempt_num, attempt_index, remaining_attempts)
                print(f"\n[AGENT] ──── Attempt {attempt_num} ({attempt_index}/{remaining_attempts}) ────")

            if unit.queued_manual_prompt:
                unit.prompt = unit.queued_manual_prompt
                unit.queued_manual_prompt = None
                unit.reference_image_path = None
                self._log.info("Unit %s: applied queued manual prompt", unit.unit_id)
                self._save_checkpoint(f"Unit {unit.unit_id} manual prompt applied")

            manual_image_ref_assets = dict(unit.queued_manual_image_ref_assets or {})

            # 1. PLAN — select skill
            skill = self.planner.select_skill(unit, excluded_skills)
            if skill is None:
                print("[AGENT] No available skill, giving up on this unit")
                return False
            current_skill = skill

            # 2. ACT — execute pipeline
            ctx = ExecutionContext(
                output_dir=self.project_state.output_directory,
                unit_id=unit.unit_id,
                attempt_number=attempt_num,
                prompt=unit.prompt,
                duration_seconds=unit.duration_seconds,
                reference_image_path=unit.reference_image_path,
                manual_image_ref_assets=manual_image_ref_assets,
                storyboard=self.storyboard,
                model=self.config.llm_model,
                scene_numbers=unit.scene_numbers,
                config=self.config,
                runtime_overrides=dict(getattr(self.config, "runtime_overrides", {}) or {}),
                prev_segment_grid_image=unit.prev_segment_grid_image,
            )

            if getattr(self.config, "enable_transition_bridge", False):
                prev_unit = self._find_prev_completed_unit(unit)
                if prev_unit and prev_unit.final_video_path:
                    ctx.prev_video_path = prev_unit.final_video_path
                    ctx.all_prev_scene_numbers = self._collect_prev_scene_numbers(unit)

            if attempt is None:
                attempt = GenerationAttempt(
                    attempt_id=attempt_num,
                    tool_used=f"skill:{skill.name}",
                    status=AttemptStatus.IN_PROGRESS,
                    input_params={
                        "skill": skill.name,
                        "prompt": unit.prompt,
                        "manual_image_ref_assets": manual_image_ref_assets,
                    },
                    max_attempts_hint=remaining_attempts,
                )
                unit.add_attempt(attempt)
                unit.consume_pending_extra_attempt()
                self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt_num} started")
            else:
                attempt.tool_used = f"skill:{skill.name}"
                attempt.input_params = {
                    **(attempt.input_params or {}),
                    "skill": skill.name,
                    "prompt": unit.prompt,
                    "manual_image_ref_assets": manual_image_ref_assets,
                }
                attempt.max_attempts_hint = remaining_attempts
                self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt_num} placeholder adopted")

            def _on_submitted(info: dict, _a=attempt, _u=unit):
                _a.metadata.update(info)
                _u.queued_manual_image_ref_assets = {}
                hid = info.get("history_id", "?")
                self._save_checkpoint(
                    f"Unit {_u.unit_id} attempt {_a.attempt_id} "
                    f"submitted (history_id={hid})")

            ctx.on_task_submitted = _on_submitted

            try:
                result = self.pipeline_executor.execute(skill, unit, ctx)
                if result.success and result.output_path:
                    attempt.status = AttemptStatus.SUCCESS
                    attempt.output_path = result.output_path
                    attempt.metadata.update(result.metadata)
                    print(f"[AGENT] Pipeline success: {result.output_path}")
                else:
                    attempt.status = AttemptStatus.FAILED
                    attempt.error_message = result.error or "No output"
                    if result.metadata:
                        attempt.metadata.update(result.metadata)
                    print(f"[AGENT] Pipeline failed: {attempt.error_message}")
            except Exception as exc:
                self._log.error("Pipeline exception: %s", exc, exc_info=True)
                attempt.status = AttemptStatus.FAILED
                attempt.error_message = str(exc)

            self._save_checkpoint(
                f"Unit {unit.unit_id} attempt {attempt_num} "
                f"{'success' if attempt.status == AttemptStatus.SUCCESS else 'failed'}")

            # 3. OBSERVE — evaluate if video was produced
            if attempt.status == AttemptStatus.SUCCESS and attempt.output_path:
                accepted = self._evaluate(unit, attempt)
                if accepted:
                    if not unit.final_attempt_locked:
                        unit.final_video_path = attempt.output_path
                        unit.final_attempt_id = attempt.attempt_id
                    unit.pending_extra_attempts = 0
                    self._save_checkpoint(f"Unit {unit.unit_id} completed")
                    return True
                else:
                    # Only mark as FAILED if critique actually ran (has result or explicit error).
                    # If critique_error is set but no critique_result, the evaluator itself failed
                    # (API error / timeout). Keep the attempt as SUCCESS so the frontend can show
                    # "critique failed: <reason>" instead of "no critique result", and so the
                    # attempt is still usable as a fallback best-attempt.
                    if attempt.critique_result is not None or attempt.critique_error is None:
                        attempt.status = AttemptStatus.FAILED
                    if unit.skip_critic_rewrite_once:
                        unit.skip_critic_rewrite_once = False
                        self._log.info(
                            "Unit %s attempt %s rejected — skip critic rewrite once enabled",
                            unit.unit_id,
                            attempt.attempt_id,
                        )
                        print("[AGENT] Skipping critic-based prompt rewrite for this attempt")
                    else:
                        self._auto_rewrite_prompt(unit, attempt)
                    self._save_checkpoint(f"Unit {unit.unit_id} prompt handling after critique")

            # After 2+ failures, check for IP issues in the prompt
            self._maybe_rewrite_for_ip(unit)
            self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt_num} done")

        best = self._pick_best_attempt(unit)
        if best and best.output_path:
            if not unit.final_attempt_locked:
                unit.final_video_path = best.output_path
                unit.final_attempt_id = best.attempt_id
            score = (best.critique_result or {}).get("overall_score", "?")
            self._log.info("Unit %s: max attempts reached — using best attempt (score=%s)",
                           unit.unit_id, score)
            print(f"[AGENT] Max attempts reached — using best attempt (score={score})")
            self._save_checkpoint(f"Unit {unit.unit_id} using best attempt")
            return True

        self._log.info("Unit %s: max attempts reached — all attempts failed", unit.unit_id)
        return False

    def _find_unit_by_id(self, unit_id: int) -> Optional[WorkUnit]:
        for unit in self.project_state.script.work_units:
            if int(unit.unit_id) == int(unit_id):
                return unit
        return None

    @staticmethod
    def _is_unit_currently_busy(unit: WorkUnit) -> bool:
        attempts = unit.attempts or []
        if any(str(a.status) == "in_progress" and not (a.metadata or {}).get("placeholder") for a in attempts):
            return True
        if not unit.is_completed and attempts:
            last = attempts[-1]
            if str(last.status) == "success" and last.output_path and not last.critique_result and not last.critique_error:
                return True
        return False

    def _apply_regen_request_to_unit(self, unit: WorkUnit, req: dict) -> None:
        manual_prompt = (req.get("manual_prompt") or "").strip()
        source_prompt = (req.get("source_prompt") or "").strip()
        manual_image_ref_assets = req.get("manual_image_ref_assets") or {}
        if not isinstance(manual_image_ref_assets, dict):
            manual_image_ref_assets = {}
        current_unit_prompt = (unit.prompt or "").strip()
        unit.pending_extra_attempts = max(1, int(req.get("extra_attempts") or 1))
        unit.is_completed = False
        unit.queued_manual_image_ref_assets = dict(manual_image_ref_assets)
        if manual_prompt:
            unit.queued_manual_prompt = manual_prompt
            unit.skip_critic_rewrite_once = True
        elif source_prompt and source_prompt != current_unit_prompt:
            unit.queued_manual_prompt = source_prompt
            unit.skip_critic_rewrite_once = False
        else:
            unit.queued_manual_prompt = None
            unit.skip_critic_rewrite_once = False
        self._log.info(
            "Queued external regen for unit %s (extra_attempts=%s, manual_refs=%s)",
            unit.unit_id,
            unit.pending_extra_attempts,
            len(unit.queued_manual_image_ref_assets or {}),
        )

    def _consume_external_regen_requests(self) -> list[int]:
        with self._external_regen_lock:
            requests = load_requests(self._run_dir)
            queued = [req for req in requests if req.get("status") == "queued"]
            if not queued:
                return []

            queued.sort(key=lambda req: float(req.get("started_at") or req.get("updated_at") or req.get("created_at") or 0), reverse=True)
            ordered_unit_ids: list[int] = []
            changed = False
            now = time.time()

            for req in queued:
                unit_id = int(req.get("unit_id", -1))
                unit = self._find_unit_by_id(unit_id)
                if not unit:
                    req["status"] = "missing_unit"
                    req["updated_at"] = now
                    changed = True
                    continue
                if self._is_unit_currently_busy(unit):
                    continue
                self._apply_regen_request_to_unit(unit, req)
                req["status"] = "consumed"
                req["consumed_at"] = now
                req["updated_at"] = now
                changed = True
                ordered_unit_ids.append(unit_id)

            if changed:
                save_requests(self._run_dir, requests)
                self._save_checkpoint("external regen requests consumed")
            return ordered_unit_ids

    # ══════════════════════════════════════════════════════════════════
    #  PARALLEL MODE — fully-async worker pool
    #
    #  Phase 0  (once) : pre-generate character / location sheets
    #  Workers  (N)    : each independently loops:
    #      grab unit → plan → generate → critique → rewrite → retry
    #      then grab the next unit from the queue.
    # ══════════════════════════════════════════════════════════════════

    def _prepare_charsheets(self):
        """Pre-generate character + location + prop reference sheets so parallel threads skip this."""
        if not self.storyboard:
            return

        characters = getattr(self.storyboard, "characters", {}) or {}
        locations = getattr(self.storyboard, "locations", {}) or {}
        sb_props = getattr(self.storyboard, "props", {}) or {}

        if not characters and not locations and not sb_props:
            return

        from tools.image_gen import (
            CHARSHEET_TEMPLATE, LOCATION_SHEET_TEMPLATE, PROP_SHEET_TEMPLATE,
            DERIVED_CHARSHEET_TEMPLATE, DERIVED_LOCATION_SHEET_TEMPLATE,
            DERIVED_PROP_SHEET_TEMPLATE,
            _rewrite_prompt_for_safety, MAX_SAFETY_REWRITES,
            analyze_and_rewrite_ip,
        )
        from clients import get_image_client
        from clients.llm_client import ImageGenerationBlockedError

        output_dir = self.project_state.output_directory
        max_retries = 10

        # ── Global style from video_analysis (fallback for descriptions missing style prefix) ──
        _va = getattr(self.storyboard, "video_analysis", None) or {}
        _global_style = (_va.get("style", "") if isinstance(_va, dict)
                         else getattr(_va, "style", "")).strip()

        # Build unified task list: (name, description, subject_type, obj_with_image_path)
        # First, recover any charsheets already on disk but not yet linked
        # (e.g. process was killed after image gen but before checkpoint save)
        _prefix_map = {"character": "charsheet", "location": "locsheet", "prop": "propsheet"}

        # Load user_overridden_sheets from checkpoint so we never regenerate
        # images the user has manually uploaded or triggered a regen for.
        _user_overridden: set[str] = set()
        try:
            _cp_path = Path(output_dir) / "checkpoint.json"
            if _cp_path.exists():
                with open(_cp_path, "r", encoding="utf-8") as _f:
                    _disk_cp_data = json.load(_f)
                _user_overridden = set(_disk_cp_data.get("user_overridden_sheets", []))
        except Exception:
            pass

        for name, obj, stype in (
            [(n, c, "character") for n, c in characters.items()] +
            [(n, l, "location") for n, l in locations.items()] +
            [(n, p, "prop") for n, p in sb_props.items()]
        ):
            if getattr(obj, "image_path", "") and Path(obj.image_path).exists():
                continue
            prefix = _prefix_map.get(stype, "charsheet")
            # Scan for existing files matching this entity (including _v2, _v3 variants)
            candidates = sorted(
                Path(output_dir).glob(f"{prefix}_{name}*.png"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if candidates:
                obj.image_path = str(candidates[0])
                key_prefix = {"character": "char", "location": "loc", "prop": "prop"}
                key = f"{key_prefix[stype]}:{name}"
                self.project_state.generated_charsheets[key] = str(candidates[0])
                print(f"[AGENT]   ♻ Recovered existing {stype} sheet for {name}: {candidates[0].name}")

        _key_prefix_for_override = {"character": "char", "location": "loc", "prop": "prop"}

        tasks: list[tuple[str, str, str, object]] = []
        derived_tasks: list[tuple[str, str, str, object]] = []
        for name, char in characters.items():
            if not char.image_path or not Path(char.image_path).exists():
                override_key = f"{_key_prefix_for_override['character']}:{name}"
                if override_key in _user_overridden:
                    print(f"[AGENT]   ⏭ Skipping character sheet for {name}: user has overridden this image")
                    continue
                if getattr(char, "_derived_from", None):
                    derived_tasks.append((name, char.description, "character", char))
                else:
                    tasks.append((name, char.description, "character", char))
        for name, loc in locations.items():
            if not loc.image_path or not Path(loc.image_path).exists():
                override_key = f"{_key_prefix_for_override['location']}:{name}"
                if override_key in _user_overridden:
                    print(f"[AGENT]   ⏭ Skipping location sheet for {name}: user has overridden this image")
                    continue
                if getattr(loc, "_derived_from", None):
                    derived_tasks.append((name, loc.description, "location", loc))
                else:
                    tasks.append((name, loc.description, "location", loc))
        for name, prop in sb_props.items():
            if not getattr(prop, "image_path", "") or not Path(prop.image_path).exists():
                override_key = f"{_key_prefix_for_override['prop']}:{name}"
                if override_key in _user_overridden:
                    print(f"[AGENT]   ⏭ Skipping prop sheet for {name}: user has overridden this image")
                    continue
                if getattr(prop, "_derived_from", None):
                    derived_tasks.append((name, prop.description, "prop", prop))
                else:
                    tasks.append((name, prop.description, "prop", prop))

        if not tasks and not derived_tasks:
            cached_names = ", ".join(
                list(characters.keys()) + list(locations.keys()) + list(sb_props.keys())
            )
            print(f"[AGENT] All sheets already exist "
                  f"({len(characters)} chars, {len(locations)} locs, "
                  f"{len(sb_props)} props): {cached_names}")
            return

        n_chars = sum(1 for _, _, t, _ in tasks if t == "character")
        n_locs = sum(1 for _, _, t, _ in tasks if t == "location")
        n_props = sum(1 for _, _, t, _ in tasks if t == "prop")
        total_chars = len(characters)
        total_locs = len(locations)
        total_props = len(sb_props)
        print(f"[AGENT] Pre-generating {n_chars}/{total_chars} character + "
              f"{n_locs}/{total_locs} location + "
              f"{n_props}/{total_props} prop sheet(s) in parallel...")

        # Save an initial checkpoint so the run is resumable even if killed during image gen
        self._save_checkpoint("charsheet generation starting")

        from tools.style_consistency_checker import verify_image_matches_prompt
        max_verify_retries = 2

        # ── Helper: charsheet key for pending_charsheet_tasks ──
        _key_prefix_map = {"character": "char", "location": "loc", "prop": "prop"}

        def _deduct_image_credit(stype: str, name: str):
            """Deduct credits for a successfully generated reference image."""
            uid = os.environ.get("VIDEO_AGENT_OWNER_USER_ID")
            if uid:
                try:
                    from dashboard.credits import COST_IMAGE_GEN, deduct_credits_standalone
                    label = {"character": "角色", "location": "场景", "prop": "道具"}.get(stype, stype)
                    ok = deduct_credits_standalone(int(uid), COST_IMAGE_GEN, "image_gen", f"参考图生成（{label}：{name}）")
                    if not ok:
                        self._log.error("参考图扣费失败: uid=%s type=%s name=%s", uid, stype, name)
                except Exception as exc:
                    self._log.error("参考图扣费异常: uid=%s — %s", uid, exc)

        def _charsheet_key(stype: str, name: str) -> str:
            return f"{_key_prefix_map.get(stype, stype)}:{name}"

        def _save_pending_task(stype: str, name: str, task_id: str):
            """Persist a submitted Nano Banana task ID so resume can recover it."""
            key = _charsheet_key(stype, name)
            self.project_state.pending_charsheet_tasks[key] = task_id
            self._save_checkpoint(f"charsheet task submitted: {name} → {task_id}")

        def _clear_pending_task(stype: str, name: str):
            key = _charsheet_key(stype, name)
            self.project_state.pending_charsheet_tasks.pop(key, None)

        def _try_recover_pending(stype: str, name: str, out_path: str, obj: object) -> tuple[str, str, bool, str] | None:
            """If there's a pending task ID from a previous run, try to poll it.
            Returns (name, stype, True, path) on success, None if we should re-submit."""
            key = _charsheet_key(stype, name)

            # Check both in-memory state AND disk checkpoint (dashboard may
            # have written a pending task after our process started).
            task_id = self.project_state.pending_charsheet_tasks.get(key)
            if not task_id:
                try:
                    cp_path = Path(self.project_state.output_directory) / "checkpoint.json"
                    if cp_path.exists():
                        with open(cp_path, "r", encoding="utf-8") as f:
                            disk_cp = json.load(f)
                        task_id = disk_cp.get("pending_charsheet_tasks", {}).get(key)
                        if task_id:
                            # Sync into memory
                            self.project_state.pending_charsheet_tasks[key] = task_id
                except Exception:
                    pass

            if not task_id:
                return None

            # Pending task recovery is not supported with synchronous image providers
            _clear_pending_task(stype, name)
            return None

        def _reload_entity_description(name: str, stype: str) -> str | None:
            """Re-read the entity description from disk.

            Checks both the run-local storyboard and the source storyboard
            (in the storyboards/ directory), preferring whichever was modified
            more recently.  This lets us pick up dashboard edits without a
            process restart.
            """
            category = {"character": "characters", "location": "locations", "prop": "props"}.get(stype)
            if not category:
                return None

            def _read_desc(path: str | Path) -> str | None:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        sb_data = json.load(f)
                    for entity in sb_data.get(category, []):
                        if entity.get("name") == name:
                            desc = entity.get("description", "")
                            personality = entity.get("personality", "")
                            if stype == "character" and personality:
                                return f"{desc}，性格：{personality}"
                            return desc
                except Exception:
                    pass
                return None

            # Collect candidate storyboard files, prefer the most recently modified
            candidates: list[Path] = []
            sb_path = self.project_state.storyboard_path
            if sb_path and Path(sb_path).exists():
                candidates.append(Path(sb_path))

            # Also check the source storyboard
            from main import _find_source_storyboard
            source = _find_source_storyboard(
                sb_path or "", self.project_state.output_directory
            )
            if source:
                candidates.append(source)

            # Sort by mtime descending — most recently edited first
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            for path in candidates:
                result = _read_desc(path)
                if result:
                    return result
            return None

        def _gen_one(idx: int, name: str, desc: str,
                     stype: str, obj: object,
                     total: int = 0,
                     reference_images: list[str] | None = None) -> tuple[str, str, bool, str]:
            """Returns (name, stype, success, message)."""
            # ── Reload description from disk (dashboard may have updated it) ──
            fresh_desc = _reload_entity_description(name, stype)
            if fresh_desc and fresh_desc != desc:
                print(f"[AGENT]   ↻ {name} description updated from disk")
                desc = fresh_desc

            # ── Sanitize name/desc for prompt (brackets can cause upstream 500) ──
            prompt_name = name.replace("[", "（").replace("]", "）")
            clean_desc = desc.replace("。。", "。")

            # ── Prepend global style to description ──
            if _global_style:
                clean_desc = f"{_global_style}，{clean_desc}"

            # ── Choose template: derived (image-editing) vs normal ──
            derived_from = getattr(obj, "_derived_from", "")
            change_desc = getattr(obj, "_change_description", "")
            is_derived = bool(derived_from and reference_images and change_desc)

            if stype == "location":
                if is_derived:
                    current_prompt = DERIVED_LOCATION_SHEET_TEMPLATE.format(
                        name=prompt_name, description=clean_desc,
                        change_description=change_desc,
                    )
                else:
                    current_prompt = LOCATION_SHEET_TEMPLATE.format(name=prompt_name, description=clean_desc)
                out_path = f"{output_dir}/locsheet_{name}.png"
            elif stype == "prop":
                if is_derived:
                    current_prompt = DERIVED_PROP_SHEET_TEMPLATE.format(
                        name=prompt_name, description=clean_desc,
                        change_description=change_desc,
                    )
                else:
                    current_prompt = PROP_SHEET_TEMPLATE.format(name=prompt_name, description=clean_desc)
                out_path = f"{output_dir}/propsheet_{name}.png"
            else:
                if is_derived:
                    current_prompt = DERIVED_CHARSHEET_TEMPLATE.format(
                        name=prompt_name, description=clean_desc,
                        change_description=change_desc,
                    )
                else:
                    current_prompt = CHARSHEET_TEMPLATE.format(name=prompt_name, description=clean_desc)
                out_path = f"{output_dir}/charsheet_{name}.png"

            effective_total = total or len(tasks)
            label = {"location": "场景", "prop": "道具"}.get(stype, "角色")
            print(f"[AGENT]   [{idx}/{effective_total}] {label} {name}: {desc[:60]}...")

            # ── Try to recover from a pending task first ──
            recovered = _try_recover_pending(stype, name, out_path, obj)
            if recovered is not None:
                return recovered

            rewrite_history: list[str] = []
            verify_failures = 0
            candidates: list[tuple[str, float]] = []  # (path, score)

            for attempt in range(1, max_retries + 1):
                cur_out = (
                    f"{out_path.rsplit('.', 1)[0]}"
                    f"{'_v' + str(verify_failures + 1) if verify_failures else ''}.png"
                )
                try:
                    client = get_image_client()

                    image_data = client.generate_image(
                        prompt=current_prompt,
                        aspect_ratio="1:1",
                        image_size="2K",
                        reference_images=reference_images,
                    )
                    remote_url = getattr(client, "last_remote_url", "") or ""

                    Path(cur_out).parent.mkdir(parents=True, exist_ok=True)
                    with open(cur_out, "wb") as f:
                        f.write(image_data)

                    # ── Verify image matches prompt ──────────────────
                    vr = verify_image_matches_prompt(
                        image_path=cur_out,
                        prompt=current_prompt,
                    )
                    if vr["passed"]:
                        print(f"[AGENT]   ✔ {name} verified (score {vr['score']:.0f}/10)")
                        _clear_pending_task(stype, name)
                        obj.image_path = cur_out
                        if remote_url:
                            obj._remote_url = remote_url
                        _deduct_image_credit(stype, name)
                        return name, stype, True, cur_out

                    verify_failures += 1
                    candidates.append((cur_out, vr["score"]))
                    issues_str = "; ".join(vr["issues"][:3]) if vr["issues"] else vr["brief"]
                    print(f"[AGENT]   ✗ {name} verify FAIL "
                          f"(score {vr['score']:.0f}/10, "
                          f"try {verify_failures}/{max_verify_retries}): "
                          f"{issues_str}")

                    if verify_failures >= max_verify_retries:
                        best_path, best_score = max(candidates, key=lambda x: x[1])
                        print(f"[AGENT]   ⚠ {name}: verify budget exhausted, "
                              f"picking best candidate (score {best_score:.0f}/10)")
                        _clear_pending_task(stype, name)
                        obj.image_path = best_path
                        if remote_url:
                            obj._remote_url = remote_url
                        _deduct_image_credit(stype, name)
                        return name, stype, True, best_path

                    _clear_pending_task(stype, name)
                    continue

                except ImageGenerationBlockedError as e:
                    self._log.warning("%s sheet blocked for %s: %s",
                                      stype, name, e.reason)
                    print(f"[AGENT]   ⚠ {name} blocked by safety: {e.reason}")
                    _clear_pending_task(stype, name)

                    if len(rewrite_history) >= MAX_SAFETY_REWRITES:
                        print(f"[AGENT]   ✗ {name}: exhausted {MAX_SAFETY_REWRITES} safety rewrites")
                        return name, stype, False, f"blocked after {MAX_SAFETY_REWRITES} rewrites: {e.reason}"

                    rw = _rewrite_prompt_for_safety(
                        current_prompt, e.reason, rewrite_history or None,
                    )
                    if not rw:
                        print(f"[AGENT]   ✗ {name}: safety rewrite LLM failed")
                        return name, stype, False, f"safety rewrite failed: {e.reason}"

                    rewrite_history.append(current_prompt)
                    current_prompt = rw["rewritten_prompt"]
                    print(f"[AGENT]   ↻ {name} rewritten: {current_prompt[:100]}...")
                    print(f"[AGENT]     Changes: {', '.join(rw['changes_made'])}")

                except Exception as e:
                    self._log.error("%s sheet gen failed for %s (attempt %d/%d): %s",
                                    stype, name, attempt, max_retries, e)
                    _clear_pending_task(stype, name)
                    if attempt < max_retries:
                        wait = min(2 ** attempt, 30)
                        print(f"[AGENT]   ✗ {name} (attempt {attempt}/{max_retries}): {e}")
                        print(f"[AGENT]     Retrying in {wait}s...")
                        time.sleep(wait)
                    else:
                        return name, stype, False, str(e)
            return name, stype, False, "unknown error"

        max_workers = min(len(tasks), 4) if tasks else 1
        if tasks:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_gen_one, i, name, desc, stype, obj): (name, stype)
                    for i, (name, desc, stype, obj) in enumerate(tasks, 1)
                }
                for fut in as_completed(futures):
                    name, stype, ok, msg = fut.result()
                    label = {"location": "场景", "prop": "道具"}.get(stype, "角色")
                    if ok:
                        key_prefix = {"character": "char", "location": "loc", "prop": "prop"}
                        key = f"{key_prefix.get(stype, stype)}:{name}"
                        self.project_state.generated_charsheets[key] = msg
                        self._save_checkpoint(f"charsheet {label} {name} done")
                        print(f"[AGENT]   ✓ {label} {name} → {msg}")
                    else:
                        print(f"[AGENT]   ✗ {label} {name}: failed after {max_retries} attempts — {msg}")

        # ── Phase 2: derived entities (need original entity images as reference) ──
        if derived_tasks:
            # Build lookup: original_name → entity (from the now-generated originals)
            all_entities = {
                **{n: c for n, c in characters.items()},
                **{n: l for n, l in locations.items()},
                **{n: p for n, p in sb_props.items()},
            }
            n_derived = len(derived_tasks)
            print(f"[AGENT] Generating {n_derived} derived entity sheet(s) "
                  f"(with original images as reference)...")

            with ThreadPoolExecutor(max_workers=min(n_derived, 4)) as pool:
                futures = {}
                for i, (name, desc, stype, obj) in enumerate(derived_tasks, 1):
                    derived_from = getattr(obj, "_derived_from", "")
                    ref_imgs = None
                    if derived_from:
                        orig = all_entities.get(derived_from)
                        if orig:
                            # Prefer remote CDN URL over local file path
                            # for better compatibility with image APIs.
                            remote = getattr(orig, "_remote_url", "")
                            if remote:
                                ref_imgs = [remote]
                                print(f"[AGENT]   ↳ {name}: using remote URL from {derived_from}")
                            elif getattr(orig, "image_path", ""):
                                # Fallback: local path (will be skipped by
                                # _prepare_reference_inputs but log a warning)
                                ref_imgs = [orig.image_path]
                                print(f"[AGENT]   ↳ {name}: no remote URL for {derived_from}, "
                                      f"falling back to local path (may not work)")
                    futures[pool.submit(
                        _gen_one, i, name, desc, stype, obj,
                        total=n_derived, reference_images=ref_imgs,
                    )] = (name, stype)

                for fut in as_completed(futures):
                    name, stype, ok, msg = fut.result()
                    label = {"location": "场景", "prop": "道具"}.get(stype, "角色")
                    if ok:
                        key_prefix = {"character": "char", "location": "loc", "prop": "prop"}
                        key = f"{key_prefix.get(stype, stype)}:{name}"
                        self.project_state.generated_charsheets[key] = msg
                        self._save_checkpoint(f"charsheet {label} {name} done")
                        print(f"[AGENT]   ✓ (衍生) {label} {name} → {msg}")
                    else:
                        print(f"[AGENT]   ✗ (衍生) {label} {name}: failed — {msg}")

    def _run_parallel(self) -> ProjectState:
        """
        Fully-async parallel mode — each worker independently handles:
        generate → critique → rewrite → retry, then picks up the next unit.
        No synchronisation between workers; feedback is immediate.
        """
        print("\n" + "=" * 70)
        print("VIDEO DIRECTOR AGENT — Starting Production (PARALLEL)")
        print("=" * 70 + "\n")
        start = time.time()

        # Install SIGTERM handler so workers can detect pause/stop early
        _STOP_EVENT.clear()
        _install_sigterm_handler()

        # Phase 0: shared assets (sequential, once)
        self._prepare_charsheets()
        self._save_checkpoint("charsheets prepared")

        # Phase 0.5: resume any in-progress attempts from a previous run
        # (e.g. videos still generating on Jimeng when process was killed)
        has_in_progress = any(
            a.status == "in_progress"
            for u in self.project_state.script.work_units
            for a in u.attempts
        )
        if has_in_progress:
            print("[PARALLEL] Resuming in-progress attempts from previous run...")
            resumed = self.resume_pending_attempts()
            if resumed:
                print(f"[PARALLEL] Recovered {resumed} attempt(s)")
            self._save_checkpoint("resume_pending done")

        pending = [u for u in self.project_state.script.work_units
                   if not u.is_completed]
        max_attempts = self.config.max_attempts_per_scene
        n_workers = self.config.max_parallel_tasks

        print(f"[PARALLEL] {len(pending)} unit(s) to generate")
        print(f"[PARALLEL] Workers: {n_workers}")
        print(f"[PARALLEL] Max attempts per unit: {max_attempts}\n")

        # ── Build dependency group chain from storyboard _meta ────
        # group_chain: scene_number -> next scene_number in same group
        # deferred_units: scene_number -> WorkUnit (waiting for predecessor)
        group_chain: Dict[int, int] = {}
        deferred_scene_numbers: set[int] = set()

        dep_groups = []
        if self.storyboard and hasattr(self.storyboard, "meta"):
            dep_groups = (self.storyboard.meta or {}).get("dependency_groups", [])

        if dep_groups:
            for g in dep_groups:
                sids = g.get("segment_ids", [])
                for i in range(len(sids) - 1):
                    group_chain[sids[i]] = sids[i + 1]
                    deferred_scene_numbers.add(sids[i + 1])
            if group_chain:
                serial_count = sum(1 for g in dep_groups if len(g.get("segment_ids", [])) > 1)
                print(f"[PARALLEL] Dependency groups: {len(dep_groups)} "
                      f"({serial_count} serial chains, "
                      f"{len(deferred_scene_numbers)} deferred units)")

        # Map scene_number -> WorkUnit for deferred lookup
        scene_to_unit: Dict[int, WorkUnit] = {}
        for u in pending:
            for sn in u.scene_numbers:
                scene_to_unit[sn] = u
        # Also map completed units by scene_number (for predecessor lookup on resume)
        all_scene_to_unit: Dict[int, WorkUnit] = {}
        for u in self.project_state.script.work_units:
            for sn in u.scene_numbers:
                all_scene_to_unit[sn] = u
        # Build reverse map: scene_number -> its predecessor scene_number
        predecessor_map: Dict[int, int] = {}
        for g in dep_groups:
            sids = g.get("segment_ids", [])
            for i in range(1, len(sids)):
                predecessor_map[sids[i]] = sids[i - 1]

        deferred_units: Dict[int, WorkUnit] = {}

        # Thread-safe work queue + dynamic priority queue for插队重生成
        work_q: queue.Queue[WorkUnit] = queue.Queue()
        for u in pending:
            # Only enqueue if none of this unit's scene_numbers are deferred
            if deferred_scene_numbers & set(u.scene_numbers):
                # ── Resume fix: if predecessor is already completed, enqueue
                # directly instead of deferring (otherwise we deadlock on resume
                # because the predecessor's _enqueue_group_successor already fired
                # in the previous run).
                pred_sn = predecessor_map.get(u.scene_numbers[0])
                pred_unit = all_scene_to_unit.get(pred_sn) if pred_sn is not None else None
                if pred_unit and pred_unit.is_completed:
                    # Predecessor done — extract grid image and enqueue immediately
                    grid_path = None
                    if pred_unit.final_video_path and not pred_unit.abandoned_no_video:
                        try:
                            from tools.video_utils import extract_smart_grid
                            # 16grid 文件名基于视频文件名
                            video_stem = Path(pred_unit.final_video_path).stem
                            grid_out = str(
                                Path(self.project_state.output_directory)
                                / f"{video_stem}_16grid.png"
                            )
                            if Path(grid_out).exists():
                                grid_path = grid_out
                            elif Path(pred_unit.final_video_path).exists():
                                grid_path = extract_smart_grid(
                                    pred_unit.final_video_path, grid_out
                                )
                        except Exception as e:
                            self._log.warning(
                                "Grid extraction on resume for seg %s: %s",
                                pred_sn, e,
                            )
                    u.prev_segment_grid_image = grid_path or u.prev_segment_grid_image
                    work_q.put(u)
                    print(f"[PARALLEL] Resume: unit {u.unit_id} (seg {u.scene_numbers[0]}) "
                          f"predecessor seg {pred_sn} already done, enqueuing directly")
                else:
                    deferred_units[u.scene_numbers[0]] = u
            else:
                work_q.put(u)

        priority_q: deque[WorkUnit] = deque()
        queue_lock = threading.Lock()
        active_unit_ids: set[int] = set()
        suppressed_normal_ids: set[int] = set()

        def _enqueue_priority_units(unit_ids: list[int]) -> None:
            if not unit_ids:
                return
            with queue_lock:
                existing_priority_ids = {u.unit_id for u in priority_q}
                for unit_id in reversed(unit_ids):
                    unit = self._find_unit_by_id(unit_id)
                    if not unit:
                        continue
                    if unit.unit_id in active_unit_ids or unit.unit_id in existing_priority_ids:
                        continue
                    priority_q.appendleft(unit)
                    existing_priority_ids.add(unit.unit_id)
                    suppressed_normal_ids.add(unit.unit_id)

        def _sync_priority_requests() -> None:
            _enqueue_priority_units(self._consume_external_regen_requests())

        def _acquire_next_unit() -> Optional[WorkUnit]:
            while True:
                _sync_priority_requests()
                with queue_lock:
                    if priority_q:
                        unit = priority_q.popleft()
                        active_unit_ids.add(unit.unit_id)
                        return unit

                try:
                    unit = work_q.get_nowait()
                except queue.Empty:
                    with queue_lock:
                        has_active = bool(active_unit_ids)
                    if not has_active and not deferred_units and self.project_state.script.is_complete():
                        return None
                    time.sleep(0.5)
                    continue

                with queue_lock:
                    if unit.unit_id in suppressed_normal_ids:
                        continue
                    active_unit_ids.add(unit.unit_id)
                    return unit

        def _release_unit(unit_id: int) -> None:
            with queue_lock:
                active_unit_ids.discard(unit_id)

        def _enqueue_group_successor(completed_unit: WorkUnit) -> None:
            """If the completed unit has a successor in a dependency group,
            extract grid frames from its video and enqueue the next unit."""
            if not group_chain:
                return
            video_path = completed_unit.final_video_path
            for sn in completed_unit.scene_numbers:
                next_sn = group_chain.get(sn)
                if next_sn is None:
                    continue
                next_unit = deferred_units.pop(next_sn, None)
                if next_unit is None:
                    continue

                # If predecessor failed (no video), still enqueue successor
                # but without grid image — let it generate independently.
                grid_path = None
                if completed_unit.abandoned_no_video or not video_path:
                    print(f"[PARALLEL] 前序 seg {sn} 失败，"
                          f"seg {next_sn} 将无前序参考图独立生成")
                elif Path(video_path).exists():
                    try:
                        from tools.video_utils import extract_smart_grid
                        video_stem = Path(video_path).stem
                        grid_out = str(
                            Path(self.project_state.output_directory)
                            / f"{video_stem}_16grid.png"
                        )
                        grid_path = extract_smart_grid(video_path, grid_out)
                        print(f"[PARALLEL] 16宫格: seg {sn} → {grid_path}")
                    except Exception as e:
                        self._log.warning(
                            "Smart grid extraction failed for seg %s: %s", sn, e,
                        )
                        print(f"[PARALLEL] 16宫格抽取失败 (seg {sn}): {e}")

                # Attach grid image path to the next unit
                next_unit.prev_segment_grid_image = grid_path
                work_q.put(next_unit)
                print(f"[PARALLEL] 入队: unit {next_unit.unit_id} "
                      f"(seg {next_sn}, 依赖 seg {sn})")

        def _worker(worker_id: int):
            """Each worker: grab unit → plan → (generate → critique → rewrite) × N → next."""
            while True:
                unit = _acquire_next_unit()
                if unit is None:
                    return

                # Check for stop/pause signal before starting any new submission
                if _STOP_EVENT.is_set():
                    print(f"[W{worker_id}] Stop signal received, exiting worker")
                    return

                try:
                    tag_base = f"W{worker_id} Unit {unit.unit_id}"
                    print(f"\n[{tag_base}] ── Starting (scenes {unit.scene_numbers}, "
                          f"{unit.duration_seconds:.0f}s) ──")

                    # Plan — select skill (rule-based, stateless → thread-safe)
                    skill = self.planner.select_skill(unit)
                    if skill is None:
                        print(f"[{tag_base}] No available skill, skipping")
                        unit.abandoned_no_video = True
                        unit.is_completed = True
                        continue

                    # Attempt loop: generate → critique → rewrite
                    remaining_attempts = self._get_remaining_attempt_budget(unit)
                    for current_round in range(1, remaining_attempts + 1):
                        # Check stop signal before each attempt
                        if _STOP_EVENT.is_set():
                            print(f"[{tag_base}] Stop signal received, aborting attempts")
                            return
                        attempt_num = unit.get_next_attempt_id()
                        tag = f"W{worker_id} Unit {unit.unit_id} attempt {attempt_num} ({current_round}/{remaining_attempts})"
                        print(f"[{tag}] Generating ({skill.display_name})...")

                        if unit.queued_manual_prompt:
                            unit.prompt = unit.queued_manual_prompt
                            unit.queued_manual_prompt = None
                            unit.reference_image_path = None
                            self._save_checkpoint(f"{tag_base} manual prompt applied")

                        manual_image_ref_assets = dict(unit.queued_manual_image_ref_assets or {})

                        ctx = ExecutionContext(
                            output_dir=self.project_state.output_directory,
                            unit_id=unit.unit_id,
                            attempt_number=attempt_num,
                            prompt=unit.prompt,
                            duration_seconds=unit.duration_seconds,
                            reference_image_path=unit.reference_image_path,
                            manual_image_ref_assets=manual_image_ref_assets,
                            storyboard=self.storyboard,
                            model=self.config.llm_model,
                            scene_numbers=unit.scene_numbers,
                            config=self.config,
                            runtime_overrides=dict(getattr(self.config, "runtime_overrides", {}) or {}),
                            prev_segment_grid_image=unit.prev_segment_grid_image,
                        )

                        # ── Generate ──
                        attempt = self._video_gen_thread(skill, unit, ctx)
                        self._save_checkpoint(f"{tag} gen done")

                        if attempt.status != AttemptStatus.SUCCESS or not attempt.output_path:
                            print(f"[{tag}] ✗ Generation failed: "
                                  f"{attempt.error_message}")
                            self._maybe_rewrite_for_ip(unit)
                            self._save_checkpoint(f"{tag} failed + rewritten")
                            continue

                        # ── Critique ──
                        print(f"[{tag}] ✓ Video ready, critiquing...")
                        accepted = self._evaluate(unit, attempt)

                        if accepted:
                            if not unit.final_attempt_locked:
                                unit.final_video_path = attempt.output_path
                                unit.final_attempt_id = attempt.attempt_id
                            unit.pending_extra_attempts = 0
                            unit.abandoned_no_video = False
                            unit.is_completed = True
                            self._save_checkpoint(f"{tag} ACCEPTED")
                            print(f"[{tag}] ✓ ACCEPTED")
                            break

                        # ── Rewrite prompt for next attempt ──
                        # Only mark FAILED if critique actually ran (has result or explicit error).
                        # If critique_error is set but no critique_result, the evaluator itself
                        # failed (API error / timeout). Keep attempt as SUCCESS so the frontend
                        # shows "critique failed: <reason>" and the attempt remains a usable fallback.
                        if attempt.critique_result is not None or attempt.critique_error is None:
                            attempt.status = AttemptStatus.FAILED
                        print(f"[{tag}] ✗ Rejected, handling next prompt...")
                        if unit.skip_critic_rewrite_once:
                            unit.skip_critic_rewrite_once = False
                            print(f"[{tag}] Skip critic rewrite once consumed")
                        else:
                            self._auto_rewrite_prompt(unit, attempt)
                        self._maybe_rewrite_for_ip(unit)
                        self._save_checkpoint(f"{tag} rejected + handled")

                    # If no attempt was accepted, pick the best one
                    if not unit.final_video_path:
                        best = self._pick_best_attempt(unit)
                        if best and best.output_path:
                            if not unit.final_attempt_locked:
                                unit.final_video_path = best.output_path
                                unit.final_attempt_id = best.attempt_id
                            score = (best.critique_result or {}).get("overall_score", "?")
                            print(f"[{tag_base}] Using best attempt (score={score})")
                            unit.abandoned_no_video = False
                            unit.is_completed = True
                        else:
                            print(f"[{tag_base}] All attempts failed")
                            unit.abandoned_no_video = True
                            unit.is_completed = True
                        self._save_checkpoint(f"{tag_base} done")
                    else:
                        unit.abandoned_no_video = False
                        unit.is_completed = True
                        self._save_checkpoint(f"{tag_base} kept existing final video")
                finally:
                    _release_unit(unit.unit_id)
                    # ── Dependency group chain: enqueue next unit if applicable ──
                    _enqueue_group_successor(unit)

        # Launch worker threads
        pool = ThreadPoolExecutor(max_workers=n_workers)
        futures = [pool.submit(_worker, i + 1) for i in range(n_workers)]
        try:
            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    self._log.error("Worker crashed: %s", exc, exc_info=True)
        except (SystemExit, KeyboardInterrupt):
            # SIGTERM/SIGKILL received — signal workers to stop and don't wait
            _STOP_EVENT.set()
            pool.shutdown(wait=False)
            raise
        finally:
            pool.shutdown(wait=False)

        # Safety net: if any deferred units were never enqueued (e.g. predecessor
        # worker crashed), mark them as abandoned so we don't lose track.
        if deferred_units:
            for sn, orphan in deferred_units.items():
                if not orphan.is_completed:
                    print(f"[PARALLEL] ⚠ Orphaned deferred unit {orphan.unit_id} "
                          f"(seg {sn}) — predecessor never completed, marking abandoned")
                    orphan.abandoned_no_video = True
                    orphan.is_completed = True
            deferred_units.clear()

        self._select_best_videos()
        self.project_state.mark_complete()
        self._save_checkpoint("production complete")
        self._print_summary(time.time() - start)
        return self.project_state

    def _video_gen_thread(
        self,
        skill: SkillDefinition,
        unit: WorkUnit,
        ctx: ExecutionContext,
    ) -> GenerationAttempt:
        """
        Worker thread target — runs the skill pipeline (charsheet lookup
        + video submit + poll + download).  Returns the attempt record.
        """
        latest_attempt = unit.get_latest_attempt()
        if latest_attempt and latest_attempt.attempt_id == ctx.attempt_number and latest_attempt.status == AttemptStatus.IN_PROGRESS and not latest_attempt.metadata.get("history_id") and not latest_attempt.output_path:
            attempt = latest_attempt
            attempt.metadata.pop("placeholder", None)
            attempt.tool_used = f"skill:{skill.name}"
            attempt.input_params = {
                **(attempt.input_params or {}),
                "skill": skill.name,
                "prompt": unit.prompt,
                "manual_image_ref_assets": getattr(ctx, "manual_image_ref_assets", {}) or {},
            }
            self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt.attempt_id} placeholder adopted")
        else:
            attempt = GenerationAttempt(
                attempt_id=ctx.attempt_number,
                tool_used=f"skill:{skill.name}",
                status=AttemptStatus.IN_PROGRESS,
                input_params={
                    "skill": skill.name,
                    "prompt": unit.prompt,
                    "manual_image_ref_assets": getattr(ctx, "manual_image_ref_assets", {}) or {},
                },
            )
            # Add attempt BEFORE execute so checkpoint captures the in-progress state
            unit.add_attempt(attempt)
            unit.consume_pending_extra_attempt()
            self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt.attempt_id} started")

        def _on_task_submitted(info: dict):
            """Called by the video tool right after submit_video returns.
            Persists task info immediately so crash-recovery can pick it up."""
            attempt.metadata.update(info)
            unit.queued_manual_image_ref_assets = {}
            hid = info.get("history_id", "?")
            self._save_checkpoint(
                f"Unit {unit.unit_id} attempt {attempt.attempt_id} "
                f"submitted (history_id={hid})")

        ctx.on_task_submitted = _on_task_submitted

        try:
            result = self.pipeline_executor.execute(skill, unit, ctx)
            if result.success and result.output_path:
                attempt.status = AttemptStatus.SUCCESS
                attempt.output_path = result.output_path
                attempt.metadata.update(result.metadata)
            else:
                attempt.status = AttemptStatus.FAILED
                attempt.error_message = result.error or "No output"
        except Exception as exc:
            attempt.status = AttemptStatus.FAILED
            attempt.error_message = str(exc)
            self._log.error("Unit %s pipeline error: %s",
                            unit.unit_id, exc, exc_info=True)

        return attempt

    # ══════════════════════════════════════════════════════════════════
    #  SHARED HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _find_prev_completed_unit(self, current: WorkUnit) -> Optional[WorkUnit]:
        """Return the work unit immediately before *current* that has a final video."""
        units = self.project_state.script.work_units
        for i, u in enumerate(units):
            if u.unit_id == current.unit_id and i > 0:
                prev = units[i - 1]
                if prev.final_video_path:
                    return prev
                break
        return None

    def _collect_prev_scene_numbers(self, current: WorkUnit) -> List[int]:
        """Return all scene_numbers from units ordered before *current*."""
        result: List[int] = []
        for u in self.project_state.script.work_units:
            if u.unit_id == current.unit_id:
                break
            result.extend(u.scene_numbers)
        return result

    def _post_process_video(self, unit: WorkUnit) -> bool:
        """Run VideoPostProcessor on the final video."""
        if unit.post_processed:
            self._log.info("Unit %s already post-processed, skipping", unit.unit_id)
            return True

        if not unit.final_video_path:
            self._log.warning("Unit %s has no final video, skipping post-process", unit.unit_id)
            return False

        print(f"[POST] Unit {unit.unit_id} — starting post-processing: {unit.final_video_path}")
        try:
            from tools.video_post_processor import VideoPostProcessor

            processor = VideoPostProcessor(
                gemini_api_key=self.config.gemini_api_key,
                asset_base_dir=self.config.asset_library_dir,
                llm_model=self.config.llm_model,
            )
            metadata = processor.process_video(
                video_path=unit.final_video_path,
                video_name=f"unit_{unit.unit_id}",
            )
            unit.post_processed = True
            unit.post_process_result = {
                "asset_folder": metadata.get("asset_folder"),
                "total_scenes": metadata.get("total_scenes", 0),
                "usable_scenes": metadata.get("usable_scenes", 0),
                "average_quality": metadata.get("average_quality", 0),
            }
            self._save_checkpoint(f"Unit {unit.unit_id} post-processed")
            print(f"[POST] Unit {unit.unit_id} — done "
                  f"({metadata.get('usable_scenes', 0)}/{metadata.get('total_scenes', 0)} usable)")
            return True
        except Exception as exc:
            self._log.error("Unit %s post-processing failed: %s", unit.unit_id, exc, exc_info=True)
            print(f"[POST] Unit {unit.unit_id} — post-processing failed: {exc}")
            self._save_checkpoint(f"Unit {unit.unit_id} post-process failed")
            return False

    @staticmethod
    def _pick_best_attempt(unit: WorkUnit) -> Optional[GenerationAttempt]:
        candidates = [a for a in unit.attempts if a.output_path and a.critique_result]
        if not candidates:
            candidates = [a for a in unit.attempts if a.output_path]
        if not candidates:
            return None
        return max(candidates,
                   key=lambda a: (a.critique_result or {}).get("overall_score", 0))

    def _select_best_videos(self):
        """Post-generation step: select best videos per segment + concat."""
        if not self.config.enable_video_selection:
            self._log.info("Video selection disabled by config")
            return

        from tools.video_selector import select_best_videos_for_project
        from tools.video_concat import concat_videos_from_timeline
        import json as _json
        import tempfile as _tempfile

        try:
            unlocked_units = [u for u in self.project_state.script.work_units if not u.final_attempt_locked]
            if unlocked_units:
                select_best_videos_for_project(
                    work_units=unlocked_units,
                    output_dir=self.project_state.output_directory,
                    storyboard=self.storyboard,
                    model=self.config.llm_model,
                )

            all_units = sorted(self.project_state.script.work_units, key=lambda u: u.unit_id)
            segment_videos: List[str] = []
            for unit in all_units:
                vp = unit.final_video_path
                if vp and Path(vp).exists():
                    segment_videos.append(vp)
                else:
                    print(f"[CONCAT] ⚠ Unit {unit.unit_id}: no video available, skipping")

            if not segment_videos:
                raise RuntimeError("No segment videos available for concatenation")

            final_path = str(Path(self.project_state.output_directory) / "final_video.mp4")
            timeline_data = {
                "timeline": [
                    {"clip_video_path": path, "segment_index": idx}
                    for idx, path in enumerate(segment_videos)
                ]
            }
            with _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
                _json.dump(timeline_data, tf, ensure_ascii=False, indent=2)
                timeline_tmp = tf.name
            try:
                result = concat_videos_from_timeline(
                    timeline_path=timeline_tmp,
                    output_path=final_path,
                    fade=0.5,
                )
            finally:
                try:
                    Path(timeline_tmp).unlink()
                except OSError:
                    pass

            if not result.get("success"):
                raise RuntimeError(result.get("error", "concat failed"))

            self._save_checkpoint("video selection + concat done")
            print(f"\n[AGENT] ✓ Final video: {final_path}")
        except Exception as exc:
            self._log.error("Select & concat failed: %s", exc, exc_info=True)
            print(f"[AGENT] ✗ Select & concat failed: {exc}")

    def _evaluate(self, unit: WorkUnit, attempt: GenerationAttempt) -> bool:
        if self.config.batch_mode:
            self._log.info("Batch mode — auto-accepting unit %s", unit.unit_id)
            print("[AGENT] Batch mode — auto-accepting (skip critique)")
            return True

        evaluator = self.tools.get(self.config.evaluator_name)
        if evaluator is None:
            self._log.warning("Evaluator '%s' not registered, auto-accepting",
                              self.config.evaluator_name)
            return True

        print("[AGENT] Critiquing result...")
        ctx = ExecutionContext(
            output_dir=self.project_state.output_directory,
            unit_id=unit.unit_id,
            attempt_number=attempt.attempt_id,
            prompt=unit.prompt,
            duration_seconds=unit.duration_seconds,
            model=self.config.llm_model,
            config=self.config,
            runtime_overrides=dict(getattr(self.config, "runtime_overrides", {}) or {}),
        )

        readable_prompt = (
            attempt.metadata.get("readable_prompt")
            or unit.prompt
            or attempt.metadata.get("prompt")
        )
        result = evaluator.execute(ctx, video_path=attempt.output_path,
                                   scene_description=readable_prompt)
        if not result.success:
            print(f"[AGENT] Critique failed: {result.error}")
            attempt.critique_error = result.error or "Critique failed"
            return False

        attempt.critique_error = None
        attempt.critique_result = result.metadata
        score = result.metadata.get("overall_score", 0)
        rec = result.metadata.get("recommendation", "RETRY")

        self._log.info("Critique — unit %s  score=%.1f  rec=%s",
                       unit.unit_id, score, rec)
        print(f"[AGENT] Critique score: {score}/10")
        print(f"[AGENT] Recommendation: {rec}")

        if result.metadata.get("critical_issues"):
            print(f"[AGENT] Critical issues: {', '.join(result.metadata['critical_issues'])}")

        return score >= self.config.accept_score_threshold and rec == "ACCEPT"

    def _extract_scene_context(self, unit: WorkUnit) -> str:
        """Extract the original storyboard scenes for this unit as readable text."""
        if not self.storyboard or not unit.scene_numbers:
            return ""

        scene_map = {s.scene_number: s for s in self.storyboard.scenes}
        parts = []
        for sn in sorted(unit.scene_numbers):
            s = scene_map.get(sn)
            if not s:
                continue

            dialogue_text = self._format_dialogue_context(s)

            parts.append(
                f"## 场景 {s.scene_number}\n"
                f"- 剧情: {s.plot_description}\n"
                f"- 画面: {s.visual_description}\n"
                f"- 角色: {', '.join(s.characters_in_scene)}\n"
                f"- 场景: {s.scene_location}\n"
                f"{dialogue_text}"
                f"- 时长: {s.duration}\n"
                f"- 机位: {s.camera_angle}\n"
                f"- 氛围: {s.mood}\n"
                f"- 光线: {s.lighting}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _format_dialogue_context(scene) -> str:
        """Build dialogue section for scene context.

        Uses structured dialogue_lines when available (novel storyboard),
        falls back to flat dialogue string (video storyboard).
        """
        if scene.dialogue_lines:
            dl_parts = []
            for dl in scene.dialogue_lines:
                speaker = dl.get("speaker", "")
                ltype = dl.get("line_type", "dialogue")
                text = dl.get("text", "")
                emotion = dl.get("emotion", "")
                emo = f"({emotion})" if emotion else ""
                type_label = {"dialogue": "说", "inner": "内心",
                              "narration": "旁白", "crowd": "群众"}.get(ltype, ltype)
                dl_parts.append(f"    [{speaker}/{type_label}{emo}] {text}")
            return "- 对白:\n" + "\n".join(dl_parts) + "\n"
        return f"- 对白: {scene.dialogue}\n"

    def _auto_rewrite_prompt(self, unit: WorkUnit, attempt: GenerationAttempt):
        if not attempt.critique_result:
            return
        rewriter = self.tools.get("rewrite_prompt")
        if rewriter is None:
            return

        print("[AGENT] Auto-rewriting prompt based on critique...")
        try:
            scene_context = self._extract_scene_context(unit)

            ctx = ExecutionContext(
                output_dir=self.project_state.output_directory,
                unit_id=unit.unit_id,
                attempt_number=attempt.attempt_id,
                prompt=unit.prompt,
                duration_seconds=unit.duration_seconds,
                model=self.config.llm_model,
                config=self.config,
                runtime_overrides=dict(getattr(self.config, "runtime_overrides", {}) or {}),
            )
            rewrite_source_prompt = (
                attempt.metadata.get("readable_prompt")
                or unit.prompt
                or attempt.metadata.get("prompt")
            )
            # Strip the grid-image prefix from the source prompt so the
            # rewriter doesn't bake it into the rewritten text.  The prefix
            # is re-added automatically by seeddance on every execution, so
            # keeping it would cause duplication.
            rewrite_source_prompt = self._strip_grid_prefix(rewrite_source_prompt)
            result = rewriter.execute(
                ctx,
                original_prompt=rewrite_source_prompt,
                critique_feedback=attempt.critique_result,
                failure_history=[a.model_dump() for a in unit.attempts],
                scene_context=scene_context,
            )
            if result.success and result.metadata.get("rewritten_prompt"):
                old = unit.prompt
                new_prompt = result.metadata["rewritten_prompt"]
                # Safety: also strip grid prefix from the rewritten output
                # in case the LLM echoed it back.
                unit.prompt = self._strip_grid_prefix(new_prompt)
                unit.reference_image_path = None
                self._log.info("Prompt rewritten\n  OLD: %s\n  NEW: %s",
                               old[:200], unit.prompt[:200])
                print(f"[AGENT] Prompt refined: {unit.prompt[:100]}...")
        except Exception as e:
            print(f"[AGENT] Prompt rewrite failed: {e}")

    # ── Grid-prefix pattern used by seeddance to prepend dependency context ──
    # Matches both the raw form ("@图片N 是上一段剧情…") and the readable form
    # ("上一镜头画面 是上一段剧情…").  The prefix ends at "从这里开始生成下面的剧情：".
    _GRID_PREFIX_RE = re.compile(
        r"^(?:@(?:图片?\d+|image\d+)|上一镜头画面)\s*"
        r"是上一段剧情发生的故事梗概.*?从这里开始生成下面的剧情：",
        re.DOTALL,
    )

    @classmethod
    def _strip_grid_prefix(cls, prompt: str) -> str:
        """Remove the grid-image dependency prefix that seeddance auto-prepends.

        This prefix is re-added on every seeddance execution, so it must not
        persist in ``unit.prompt`` — otherwise it doubles up on retries.
        """
        if not prompt:
            return prompt
        return cls._GRID_PREFIX_RE.sub("", prompt, count=1).lstrip()

    _IMAGE_OR_REVIEW_KEYWORDS = ("图片", "人脸", "素材", "不符合平台规则", "未通过审核", "审核")

    def _errors_are_image_or_review(self, unit: WorkUnit) -> bool:
        """Return True if ALL recent errors point to image/review issues, not text."""
        recent_errors = [
            a.error_message for a in unit.attempts
            if a.status == AttemptStatus.FAILED and a.error_message
        ][-3:]
        if not recent_errors:
            return False
        return all(
            any(kw in msg for kw in self._IMAGE_OR_REVIEW_KEYWORDS)
            for msg in recent_errors
        )

    def _maybe_rewrite_for_ip(self, unit: WorkUnit):
        """After 2+ failures, analyze the prompt for IP / safety / content issues and rewrite.

        Skips rewriting if the errors are image/review related (not text issues).
        """
        fail_count = unit.get_failed_attempts_count()
        if fail_count < 2:
            return

        if getattr(unit, "_ip_checked", False):
            return

        if self._errors_are_image_or_review(unit):
            self._log.info(
                "Unit %s: skipping prompt rewrite — errors are image/review related",
                unit.unit_id,
            )
            print(f"[AGENT] Unit {unit.unit_id}: errors are image/review related, "
                  f"skipping text rewrite")
            return

        from tools.image_gen import analyze_and_rewrite_prompt_issues

        error_msgs = [
            a.error_message for a in unit.attempts
            if a.status == AttemptStatus.FAILED and a.error_message
        ]

        print(f"[AGENT] Unit {unit.unit_id}: {fail_count} failures — "
              f"analyzing prompt for potential issues (IP, safety, violence, etc.)...")
        result = analyze_and_rewrite_prompt_issues(unit.prompt, error_msgs)

        if result is None:
            print(f"[AGENT] Prompt analysis LLM failed, skipping")
            return

        unit._ip_checked = True  # type: ignore[attr-defined]

        if not result.get("has_issues"):
            print(f"[AGENT] No prompt issues detected")
            return

        detected = result.get("detected_issues", [])
        _CATEGORY_LABELS = {
            "ip": "IP/版权",
            "sexual": "色情/暗示",
            "violence": "暴力/血腥",
            "minors_in_danger": "未成年人+危险",
            "hate_extremism": "仇恨/极端",
            "other": "其他",
        }
        for d in detected:
            cat = _CATEGORY_LABELS.get(d["category"], d["category"])
            print(f"[AGENT]   ⚠ [{cat}] \"{d['original_text']}\" → \"{d['replacement']}\"")
            print(f"[AGENT]     Detail: {d['detail']}")
        print(f"[AGENT] Rationale: {result.get('rationale', '')[:200]}")

        new_prompt = result.get("rewritten_prompt", "")
        if new_prompt and new_prompt != unit.prompt:
            old_snippet = unit.prompt[:80]
            unit.prompt = new_prompt
            unit.reference_image_path = None
            print(f"[AGENT] Prompt rewritten:")
            print(f"[AGENT]   Before: {old_snippet}...")
            print(f"[AGENT]   After:  {new_prompt[:80]}...")
            issue_summary = ", ".join(
                f"{d['category']}:{d['original_text']}" for d in detected
            )
            self._log.info(
                "Prompt issue rewrite for unit %s — issues: %s\n  OLD: %s\n  NEW: %s",
                unit.unit_id, issue_summary,
                unit.prompt[:300], new_prompt[:300],
            )

    def _save_checkpoint(self, reason: str = "Checkpoint"):
        with self._checkpoint_lock:
            try:
                path = self.project_state.save_checkpoint()
                self._log.info("Checkpoint saved: %s — %s", path, reason)
            except Exception as e:
                self._log.error("Checkpoint save failed: %s", e)

    def resume_pending_attempts(self):
        """重启后恢复：对所有 IN_PROGRESS 的 attempt 继续轮询，而不是重新提交。

        从 checkpoint 里读取 history_id / backend，
        调用对应 client 继续等待并下载结果。
        适用于 server 重启后视频还在云端生成中的情况。
        """
        from clients import get_seeddance_client
        from models import AttemptStatus

        resumed = 0
        for unit in self.project_state.script.work_units:
            if unit.is_completed:
                continue

            for attempt in unit.attempts:
                if attempt.status != AttemptStatus.IN_PROGRESS:
                    continue

                metadata = attempt.metadata or {}
                history_id = metadata.get("history_id")
                backend = (metadata.get("backend") or "jimeng").lower()

                if not history_id:
                    self._log.warning(
                        "Unit %d attempt %d: IN_PROGRESS but no history_id in metadata, skipping",
                        unit.unit_id,
                        attempt.attempt_id,
                    )
                    attempt.status = AttemptStatus.FAILED
                    attempt.error_message = "Interrupted — no task ID to resume"
                    self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt.attempt_id} resume missing history_id")
                    continue

                self._log.info(
                    "Resuming Unit %d attempt %d: backend=%s history_id=%s",
                    unit.unit_id,
                    attempt.attempt_id,
                    backend,
                    history_id,
                )

                try:
                    client = get_seeddance_client()
                    result = client.wait_for_video(history_id)

                    if result and result.get("url"):
                        video_url = result["url"]
                        out_path = attempt.output_path or str(
                            Path(self.project_state.output_directory)
                            / f"unit_{unit.unit_id}_attempt_{attempt.attempt_id}.mp4"
                        )
                        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                        success = client.download_video_file(video_url, out_path)
                        if success:
                            attempt.status = AttemptStatus.SUCCESS
                            attempt.output_path = out_path
                            attempt.error_message = None
                            attempt.metadata["backend"] = backend
                            attempt.metadata["video_url"] = video_url
                            if thread_id:
                                attempt.metadata["thread_id"] = thread_id
                            if not unit.final_video_path:
                                unit.final_video_path = out_path
                            unit.is_completed = True
                            unit.abandoned_no_video = False
                            self._log.info(
                                "Unit %d attempt %d resumed successfully via %s",
                                unit.unit_id,
                                attempt.attempt_id,
                                backend,
                            )
                            resumed += 1
                        else:
                            attempt.status = AttemptStatus.FAILED
                            attempt.error_message = "Resume: download failed"
                    else:
                        attempt.status = AttemptStatus.FAILED
                        attempt.error_message = "Resume: polling returned no URL"
                except Exception as e:
                    self._log.error(
                        "Resume failed for Unit %d attempt %d via %s: %s",
                        unit.unit_id,
                        attempt.attempt_id,
                        backend,
                        e,
                    )
                    attempt.status = AttemptStatus.FAILED
                    attempt.error_message = f"Resume error: {e}"

                self._save_checkpoint(f"Unit {unit.unit_id} attempt {attempt.attempt_id} resume done")

        self._log.info("resume_pending_attempts: %d attempt(s) resumed", resumed)
        return resumed

    def _print_summary(self, elapsed: float):
        self.project_state.update_stats()
        print("\n" + "=" * 70)
        mode_label = "BATCH" if self.config.batch_mode else "Production"
        print(f"VIDEO DIRECTOR AGENT - {mode_label} Complete")
        print("=" * 70)
        if self.config.batch_mode:
            print("Mode: BATCH (raw material output, no final editing)")
        print(f"Total time: {elapsed:.1f}s")
        completed = sum(1 for u in self.project_state.script.work_units if u.final_video_path)
        post_done = sum(1 for u in self.project_state.script.work_units if u.post_processed)
        print(f"Units completed: {completed}/{len(self.project_state.script.work_units)}")
        print(f"Units post-processed: {post_done}/{len(self.project_state.script.work_units)}")
        print(f"Total attempts: {self.project_state.total_attempts}")
        print("=" * 70 + "\n")
