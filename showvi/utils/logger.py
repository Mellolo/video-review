"""
Centralized logging for the Video Director Agent.

Call ``setup_run_logging(run_dir)`` once at startup to create three log files:
  - agent.log  — decisions, tool dispatch, escalation, results
  - critic.log — VLM inputs / outputs
  - sora.log   — API requests, polling, downloads
"""

import json
import logging
from pathlib import Path
from typing import Optional, Any

_run_dir: Optional[Path] = None


def setup_run_logging(run_dir: str) -> Path:
    global _run_dir
    _run_dir = Path(run_dir)
    _run_dir.mkdir(parents=True, exist_ok=True)

    _configure_file_logger("video_agent.agent", _run_dir / "agent.log")
    _configure_file_logger("video_agent.critic", _run_dir / "critic.log")
    _configure_file_logger("video_agent.sora", _run_dir / "sora.log")
    _configure_file_logger("video_agent.rewriter", _run_dir / "rewriter.log")
    _configure_file_logger("video_agent.image_gen", _run_dir / "image_gen.log")
    _configure_file_logger("video_agent.seeddance", _run_dir / "seeddance.log")
    _configure_file_logger("video_agent.storyboard_gen", _run_dir / "storyboard_gen.log")

    logging.getLogger("video_agent.agent").info(
        "Run logging initialized — run_dir: %s", _run_dir
    )
    return _run_dir


def get_run_dir() -> Optional[Path]:
    return _run_dir


def _configure_file_logger(name: str, log_path: Path) -> None:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)


def log_block(logger_name: str, title: str, data: Any) -> None:
    logger = logging.getLogger(logger_name)
    try:
        body = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        body = str(data)
    logger.info("┌─ %s\n%s", title, body)
