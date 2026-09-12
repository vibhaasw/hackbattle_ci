"""Release-gate decisions, including the Phase 3 meeting / focus-mode gate."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.config import ROOT, Settings
from src.queue import NotificationQueue

logger = logging.getLogger(__name__)


class ReleaseLogic:
    """Decide whether the queue may be shown to the developer."""

    def __init__(
        self,
        queue: NotificationQueue,
        settings: Settings,
        *,
        focus_mode_override: bool | None = None,
        git_root: Path | None = None,
        interval_seconds: int | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.queue = queue
        self.settings = settings
        self.focus_mode_override = focus_mode_override
        self.gate_warning: str | None = None
        self.git_root = Path(git_root) if git_root is not None else ROOT
        self.interval_seconds_override = interval_seconds
        self._now = now or time.time
        self._last_git_fingerprint: str | None = None
        self._timer_started: float | None = None

    def should_release(self, *, manual: bool = False) -> bool:
        """True when there is something to show, the gate is clear, and a trigger fires."""
        if not self.queue.get_pending():
            return False
        if not self._calendar_gate_clear():
            return False
        if manual and self.settings.manual_trigger_enabled:
            return True
        if self._git_commit_detected():
            return True
        if self._build_success_detected():
            return True
        if self._timer_elapsed():
            return True
        return False

    def is_held(self) -> bool:
        """True when items are waiting but the meeting/focus gate is blocking release."""
        return bool(self.queue.get_pending()) and not self._calendar_gate_clear()

    def manual_release(self) -> list[dict[str, Any]]:
        """Release the pending queue via the manual trigger, if the gate allows it."""
        if self.is_held():
            logger.info("Manual release held: focus mode is on (simulated meeting)")
            return []
        if not self.should_release(manual=True):
            logger.info("Manual release requested but nothing to show")
            return []
        snapshot = self.queue.get_queue_snapshot()
        self._record_release(len(snapshot), kind="Manual")
        return snapshot

    def auto_release(self) -> list[dict[str, Any]]:
        """Release when a non-manual trigger fires (git commit, timer, etc.)."""
        if self.is_held():
            logger.info("Auto release held: focus mode is on (simulated meeting)")
            return []
        if not self.should_release(manual=False):
            return []
        snapshot = self.queue.get_queue_snapshot()
        self._record_release(len(snapshot), kind="Auto")
        return snapshot

    def _record_release(self, count: int, *, kind: str) -> None:
        """Stamp last_release_at, bump analytics, and persist. Shared by every trigger."""
        from src.analytics import on_release

        self.queue.stats["last_release_at"] = self._now()
        on_release(self.queue)
        logger.info("%s release of %s notification(s)", kind, count)

    def set_focus_mode(self, enabled: bool) -> None:
        """Persist the focus-mode toggle into queue settings."""
        self.queue.queue_settings["focus_mode"] = enabled
        self.queue.save()
        logger.info("Focus mode %s", "on" if enabled else "off")

    def _calendar_gate_clear(self) -> bool:
        """False while focus mode is on; calendar auth failures fail open (TRD §6)."""
        if not self.settings.calendar_gate_enabled:
            return True
        if self._focus_mode_on():
            logger.info("Release held: focus mode is on")
            return False
        if self._calendar_credentials_broken():
            self.gate_warning = "Calendar credentials unreadable — failing open (not in a meeting)."
            logger.warning(self.gate_warning)
            return True
        return True

    def _calendar_credentials_broken(self) -> bool:
        """True when a credentials file was provided but cannot be read."""
        path = self.settings.google_calendar_credentials_file
        if path is None:
            return False
        if not path.exists():
            return True
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return True
        return False

    def _focus_mode_on(self) -> bool:
        if self.focus_mode_override is not None:
            return self.focus_mode_override
        stored = self.queue.queue_settings.get("focus_mode")
        if stored is not None:
            return bool(stored)
        return self.settings.focus_mode

    def _git_commit_detected(self) -> bool:
        """True once .git/HEAD's resolved SHA/mtime changes after the baseline."""
        if not self.settings.git_commit_trigger:
            return False
        try:
            current = self._git_fingerprint()
        except Exception:
            logger.exception("Git HEAD check failed under %s", self.git_root)
            return False
        if current is None:
            return False
        if self._last_git_fingerprint is None:
            self._last_git_fingerprint = current
            logger.info("Git baseline at %s", current.split(":", 1)[0][:12])
            return False
        if current == self._last_git_fingerprint:
            return False
        self._last_git_fingerprint = current
        logger.info("Git commit detected (%s)", current.split(":", 1)[0][:12])
        return True

    def _git_fingerprint(self) -> str | None:
        """Return `sha:mtime` for .git/HEAD, or None if this is not a git work tree."""
        head = self.git_root / ".git" / "HEAD"
        if not head.exists():
            return None
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            ref_path = self.git_root / ".git" / text[4:].strip()
            sha = ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else text
        else:
            sha = text
        return f"{sha}:{head.stat().st_mtime_ns}"

    def _build_success_detected(self) -> bool:
        """Stub — build-success watch is not required for the Phase 3 checkpoint."""
        return False

    def _interval_seconds(self) -> int:
        """Queue-state interval (default 3600), or the watch --interval-seconds override."""
        if self.interval_seconds_override is not None:
            return int(self.interval_seconds_override)
        stored = self.queue.queue_settings.get("check_interval_seconds")
        if stored is not None:
            return int(stored)
        return int(self.settings.check_interval_seconds)

    def _last_release_at(self) -> float | None:
        raw = self.queue.stats.get("last_release_at")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _timer_elapsed(self) -> bool:
        """True once check_interval_seconds has passed since the last release (or watch start)."""
        interval = self._interval_seconds()
        if interval <= 0:
            return False
        now = self._now()
        last = self._last_release_at()
        if last is None:
            if self._timer_started is None:
                self._timer_started = now
                logger.info("Timer baseline; next release in %ss", interval)
                return False
            last = self._timer_started
        elapsed = now - last
        if elapsed < interval:
            return False
        logger.info("Timer elapsed (%.1fs >= %ss)", elapsed, interval)
        return True
