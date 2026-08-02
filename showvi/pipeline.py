"""
Skill-driven Pipeline Executor.

Reads declarative Skill definitions (YAML) and executes them step-by-step
using the existing Tool layer.  Adding a new video model only requires
adding a new YAML file under ``skills/``.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from models import WorkUnit
from tools.base import BaseTool, ExecutionContext, ToolResult

_log = logging.getLogger("video_agent.pipeline")


# ── Skill definition (loaded from YAML) ─────────────────────────────

class SkillDefinition:
    """In-memory representation of a single Skill YAML file."""

    def __init__(self, config: dict, source_path: str = ""):
        self.name: str = config["name"]
        self.display_name: str = config.get("display_name", self.name)
        self.version: str = config.get("version", "1.0")
        self.constraints: dict = config.get("constraints", {})
        self.steps: list = config.get("steps", [])
        self.fallback: dict = config.get("fallback", {})
        self.when_to_use: str = config.get("when_to_use", "")
        self.when_not_to_use: str = config.get("when_not_to_use", "")
        self.prompt_tips: str = config.get("prompt_tips", "")
        self.priority: int = config.get("priority", 99)
        self.source_path = source_path

    # ── availability checks ──

    def is_available(self) -> bool:
        """True when every required environment variable is set."""
        for env_var in self.constraints.get("required_env", []):
            if not os.getenv(env_var):
                return False
        return True

    def supports_duration(self, duration: float) -> bool:
        min_d = self.constraints.get("min_duration", 0)
        max_d = self.constraints.get("max_duration", float("inf"))
        return min_d <= duration <= max_d

    def has_required_tools(self, tools: Dict[str, BaseTool]) -> bool:
        """True when every tool referenced by steps is registered."""
        for step in self.steps:
            if step["tool"] not in tools:
                return False
        return True

    # ── description for Planner / LLM ──

    def to_planner_description(self) -> str:
        lines = [
            f"### {self.display_name} (`{self.name}`)",
            f"**适用场景:**\n{self.when_to_use}",
            f"**不适用场景:**\n{self.when_not_to_use}",
            f"**约束:** 时长 {self.constraints.get('min_duration', '?')}"
            f"-{self.constraints.get('max_duration', '?')}s, "
            f"宽高比 {self.constraints.get('aspect_ratios', ['any'])}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Skill {self.name} v{self.version} priority={self.priority}>"


# ── Generic pipeline executor ────────────────────────────────────────

class SkillPipelineExecutor:
    """Execute any Skill by interpreting its ``steps`` list."""

    def __init__(self, tools: Dict[str, BaseTool], debug: bool = False):
        self.tools = tools
        self.debug = debug

    def execute(
        self,
        skill: SkillDefinition,
        unit: WorkUnit,
        context: ExecutionContext,
    ) -> ToolResult:
        _log.info(
            "Pipeline START  skill=%s  unit=%s  duration=%.1fs",
            skill.name, unit.unit_id, unit.duration_seconds,
        )
        print(f"[PIPELINE] Skill: {skill.display_name}")

        step_outputs: Dict[str, Any] = {}
        last_result: Optional[ToolResult] = None

        for idx, step in enumerate(skill.steps):
            step_name = step.get("name", f"step_{idx}")
            tool_name = step["tool"]
            condition = step.get("condition")
            params_tpl = step.get("params", {})
            output_mapping = step.get("output_mapping")
            on_failure = step.get("on_failure", "abort")
            max_retries = step.get("max_retries", 0)

            # 1. condition ─────────────────────────────────────────
            if condition and not self._eval_condition(condition, context, step_outputs, unit):
                _log.info("  SKIP [%s] — condition false: %s", step_name, condition)
                print(f"[PIPELINE]   Skip: {step_name}")
                continue

            # 2. tool lookup ───────────────────────────────────────
            tool = self.tools.get(tool_name)
            if tool is None:
                msg = f"Tool not registered: {tool_name}"
                _log.error("  %s", msg)
                if on_failure == "skip":
                    continue
                return ToolResult(success=False, error=msg)

            # 3. resolve params ────────────────────────────────────
            params = self._resolve_params(params_tpl, context, step_outputs, unit)

            # 3.5 debug prompt ─────────────────────────────────────
            if self.debug:
                action = self._debug_prompt(idx, step_name, tool_name, params,
                                            context, step_outputs, skill)
                if action == "skip":
                    print(f"[DEBUG]   Skipped: {step_name}")
                    continue
                elif action == "abort":
                    print(f"[DEBUG]   Aborted pipeline")
                    return ToolResult(success=False, error="Aborted by user in debug mode")

            # 4. execute with retries ──────────────────────────────
            result: Optional[ToolResult] = None
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    _log.info("  RETRY [%s] %d/%d", step_name, attempt, max_retries)
                    print(f"[PIPELINE]   Retry: {step_name} ({attempt}/{max_retries})")

                print(f"[PIPELINE]   Run: {step_name} → {tool_name}")
                try:
                    result = tool.execute(context, **params)
                except Exception as exc:
                    _log.error("  [%s] exception: %s", step_name, exc, exc_info=True)
                    result = ToolResult(success=False, error=str(exc))

                if result and result.success:
                    break

            # 5. handle failure ────────────────────────────────────
            if result is None or not result.success:
                err = result.error if result else "no result"
                _log.warning("  FAIL [%s]: %s", step_name, err)
                if on_failure == "skip":
                    print(f"[PIPELINE]   {step_name} failed — skipping")
                    continue
                print(f"[PIPELINE]   {step_name} failed — aborting pipeline")
                return result or ToolResult(success=False, error=f"Step {step_name} failed")

            # 6. output mapping ────────────────────────────────────
            if output_mapping and result.success:
                # Keys that should only update context, not the persistent unit.
                # assembled transition prompts must NOT overwrite unit.prompt,
                # otherwise on retry the prompt is double-wrapped with the
                # "（这是前序剧情：...）以下是后续剧情..." prefix.
                _context_only_keys = {"prompt"}
                for key, expr in output_mapping.items():
                    value = self._eval_output_expr(expr, result)
                    step_outputs[key] = value
                    if hasattr(context, key):
                        setattr(context, key, value)
                    if key not in _context_only_keys and hasattr(unit, key):
                        setattr(unit, key, value)
                    _log.info("  MAP %s = %s", key, str(value)[:120])

            # 6.5 debug: show result ──────────────────────────────
            if self.debug and result and result.success:
                print(f"[DEBUG]   ✓ {step_name} succeeded")
                if result.output_path:
                    print(f"[DEBUG]     output: {result.output_path}")
                for k, v in (result.metadata or {}).items():
                    print(f"[DEBUG]     {k}: {str(v)[:100]}")

            last_result = result

        if last_result is None:
            return ToolResult(success=False, error="Skill executed no steps")

        _log.info("Pipeline DONE  skill=%s  success=%s", skill.name, last_result.success)
        return last_result

    # ── debug ─────────────────────────────────────────────────────

    @staticmethod
    def _debug_prompt(step_idx: int, step_name: str, tool_name: str,
                      params: dict, context: ExecutionContext,
                      step_outputs: dict, skill: SkillDefinition) -> str:
        """
        Interactive debug prompt before each step.
        Returns: "run", "skip", or "abort".
        """
        print()
        print(f"[DEBUG] {'─' * 50}")
        print(f"[DEBUG] Step {step_idx + 1}/{len(skill.steps)}: {step_name}")
        print(f"[DEBUG]   Tool: {tool_name}")
        print(f"[DEBUG]   Unit: {context.unit_id}  Duration: {context.duration_seconds}s")
        print(f"[DEBUG]   Prompt: {context.prompt[:120]}...")
        if context.reference_image_path:
            print(f"[DEBUG]   Ref image: {context.reference_image_path}")
        if params:
            print(f"[DEBUG]   Params:")
            for k, v in params.items():
                print(f"[DEBUG]     {k}: {str(v)[:100]}")
        if step_outputs:
            print(f"[DEBUG]   Previous outputs:")
            for k, v in step_outputs.items():
                print(f"[DEBUG]     {k}: {str(v)[:100]}")
        print(f"[DEBUG] {'─' * 50}")

        while True:
            try:
                ans = input("[DEBUG] Execute? [Y/n/s(skip)/q(abort)] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return "abort"
            if ans in ("", "y", "yes"):
                return "run"
            elif ans in ("n", "s", "skip"):
                return "skip"
            elif ans in ("q", "quit", "abort"):
                return "abort"
            else:
                print("[DEBUG] Invalid input. Y=run, s=skip, q=abort")

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _eval_condition(expr: str, context: ExecutionContext,
                        step_outputs: dict, unit: WorkUnit) -> bool:
        try:
            return bool(eval(expr, {"__builtins__": {}}, {
                "context": context,
                "step_outputs": step_outputs,
                "unit": unit,
                "len": len,
                "bool": bool,
                "not": lambda x: not x,
                "getattr": getattr,
                "hasattr": hasattr,
            }))
        except Exception as exc:
            _log.warning("Condition eval failed (%s): %s", expr, exc)
            return False

    @staticmethod
    def _resolve_params(
        template: dict,
        context: ExecutionContext,
        step_outputs: dict,
        unit: WorkUnit,
    ) -> dict:
        """Replace ``${var}`` placeholders with runtime values."""
        resolved: Dict[str, Any] = {}
        for key, value in template.items():
            if isinstance(value, str) and "${" in value:
                var_name = value.strip().removeprefix("${").removesuffix("}")
                if var_name in step_outputs:
                    resolved[key] = step_outputs[var_name]
                elif hasattr(context, var_name):
                    resolved[key] = getattr(context, var_name)
                elif hasattr(unit, var_name):
                    resolved[key] = getattr(unit, var_name)
                elif var_name == "duration":
                    resolved[key] = int(context.duration_seconds)
                else:
                    _log.warning("Unresolved param ${%s}, keeping raw", var_name)
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _eval_output_expr(expr: str, result: ToolResult) -> Any:
        try:
            return eval(expr, {"__builtins__": {}}, {"result": result})
        except Exception:
            return None


# ── Built-in skill definitions ────────────────────────────────────────

_BUILTIN_SKILLS: list[dict] = [
    {
        "name": "seeddance_2_0_charsheet",
        "display_name": "Seeddance 2.0 四视图人物+场景模式",
        "version": "1.1",
        "constraints": {
            "min_duration": 4,
            "max_duration": 60,
            "required_env": ["SEEDDANCE_SESSION_ID"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "max_images": 10,
            "supports_text_only": False,
        },
        "steps": [
            {
                "name": "generate_character_location_and_prop_sheets",
                "tool": "image_gen",
                "condition": "not context.reference_image_path",
                "params": {"image_mode": "charsheet", "aspect_ratio": "1:1"},
                "output_mapping": {
                    "charsheet_image_paths": "result.metadata.get('all_image_paths', [result.output_path])",
                    "charsheet_character_names": "result.metadata.get('character_names', [])",
                    "charsheet_location_names": "result.metadata.get('location_names', [])",
                    "charsheet_prop_names": "result.metadata.get('prop_names', [])",
                },
                "on_failure": "abort",
                "max_retries": 10,
            },
            {
                "name": "prepare_scene_transition",
                "tool": "transition_bridge",
                "condition": "getattr(context.config, 'enable_transition_bridge', False)",
                "params": {
                    "image_paths": "${charsheet_image_paths}",
                    "characters": "${charsheet_character_names}",
                    "locations": "${charsheet_location_names}",
                    "props": "${charsheet_prop_names}",
                },
                "output_mapping": {
                    "charsheet_image_paths": "result.metadata.get('image_paths')",
                    "charsheet_character_names": "result.metadata.get('characters', [])",
                    "charsheet_location_names": "result.metadata.get('locations', [])",
                    "charsheet_prop_names": "result.metadata.get('props', [])",
                    "prompt": "result.metadata.get('assembled_prompt')",
                },
                "on_failure": "skip",
                "max_retries": 1,
            },
            {
                "name": "generate_video",
                "tool": "seeddance_image_to_video",
                "condition": None,
                "params": {
                    "model": "seedance-2.0",
                    "image_paths": "${charsheet_image_paths}",
                    "aspect_ratio": "16:9",
                    "duration": "${duration}",
                    "auto_ref": True,
                    "characters": "${charsheet_character_names}",
                    "locations": "${charsheet_location_names}",
                    "props": "${charsheet_prop_names}",
                },
                "output_mapping": None,
                "on_failure": "abort",
                "max_retries": 0,
            },
        ],
        "fallback": {},
        "when_to_use": "需要强人物一致性的场景",
        "when_not_to_use": "不需要角色一致性的快剪场景",
        "prompt_tips": "prompt 重点描述角色动作和环境，外观由四视图决定",
        "priority": 1,
    },
]


# ── Skill loader ─────────────────────────────────────────────────────

def load_skills(skills_dir: str = "skills") -> Dict[str, SkillDefinition]:
    """Load skills from built-in definitions, optionally overridden by YAML files."""
    skills: Dict[str, SkillDefinition] = {}

    for config in _BUILTIN_SKILLS:
        skill = SkillDefinition(config, source_path="<builtin>")
        skills[skill.name] = skill

    skills_path = Path(skills_dir)
    if skills_path.exists():
        for yaml_file in sorted(skills_path.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as fh:
                    config = yaml.safe_load(fh)
                if not config or "name" not in config:
                    continue
                skill = SkillDefinition(config, source_path=str(yaml_file))
                skills[skill.name] = skill
            except Exception as exc:
                _log.error("Failed to load skill %s: %s", yaml_file, exc)

    print(f"[PIPELINE] Loaded {len(skills)} skill(s): {', '.join(skills.keys())}")
    return skills
