"""Runtime settings loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    raw = _str(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


@dataclass(frozen=True)
class Settings:
    """Process configuration. Values come from `.env` / the environment."""

    github_webhook_host: str
    github_webhook_port: int
    github_webhook_secret: str
    github_token: str
    ollama_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    queue_file_path: Path
    check_interval_seconds: int
    git_commit_trigger: bool
    build_success_trigger: bool
    manual_trigger_enabled: bool
    max_queue_size: int
    auto_clear_after_days: int
    calendar_gate_enabled: bool
    avg_context_switch_cost_minutes: float

    @classmethod
    def load(cls) -> Settings:
        """Load settings from environment variables."""
        return cls(
            github_webhook_host=_str("GITHUB_WEBHOOK_HOST", "0.0.0.0"),
            github_webhook_port=_int("GITHUB_WEBHOOK_PORT", 9001),
            github_webhook_secret=_str("GITHUB_WEBHOOK_SECRET"),
            github_token=_str("GITHUB_TOKEN"),
            ollama_url=_str("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
            ollama_model=_str("OLLAMA_MODEL", "neural-chat"),
            ollama_timeout_seconds=_int("OLLAMA_TIMEOUT_SECONDS", 8),
            queue_file_path=_path("QUEUE_FILE_PATH", "queue.json"),
            check_interval_seconds=_int("CHECK_INTERVAL_SECONDS", 3600),
            git_commit_trigger=_bool("GIT_COMMIT_TRIGGER", True),
            build_success_trigger=_bool("BUILD_SUCCESS_TRIGGER", True),
            manual_trigger_enabled=_bool("MANUAL_TRIGGER_ENABLED", True),
            max_queue_size=_int("MAX_QUEUE_SIZE", 50),
            auto_clear_after_days=_int("AUTO_CLEAR_AFTER_DAYS", 7),
            calendar_gate_enabled=_bool("CALENDAR_GATE_ENABLED", True),
            avg_context_switch_cost_minutes=float(
                os.getenv("AVG_CONTEXT_SWITCH_COST_MINUTES", "17.5")
            ),
        )
