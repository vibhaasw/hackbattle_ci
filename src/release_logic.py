"""Release-gate decisions. Phase 1: manual trigger is live; others are stubs."""

from __future__ import annotations

import logging
from typing import Any

from src.config import Settings
from src.queue import NotificationQueue

logger = logging.getLogger(__name__)


class ReleaseLogic:
    """Decide whether the queue may be shown to the developer."""

    def __init__(self, queue: NotificationQueue, settings: Settings) -> None:
        self.queue = queue
        self.settings = settings

    def should_release(self, *, manual: bool = False) -> bool:
        """True when there is something to show and a trigger fires."""
        if not self.queue.get_pending():
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

    def manual_release(self) -> list[dict[str, Any]]:
        """Release the pending queue via the manual trigger."""
        if not self.should_release(manual=True):
            logger.info("Manual release requested but nothing to show")
            return []
        snapshot = self.queue.get_queue_snapshot()
        logger.info("Manual release of %s notification(s)", len(snapshot))
        return snapshot

    def _git_commit_detected(self) -> bool:
        """Stub — commit-watch lands in a later phase."""
        return False

    def _build_success_detected(self) -> bool:
        """Stub — build-success watch lands in a later phase."""
        return False

    def _timer_elapsed(self) -> bool:
        """Stub — interval timer lands in a later phase."""
        return False
