"""JSON-backed notification queue with dedup and snapshot access."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from src.config import Settings
from src.notification import Notification

logger = logging.getLogger(__name__)


class NotificationQueue:
    """Persist notifications to disk; dedup key is `{source}-{type}-{raw_id}`."""

    def __init__(self, file_path: Path | str, settings: Settings) -> None:
        self.file_path = Path(file_path)
        self.settings = settings
        self.notifications: dict[str, Notification] = {}
        self.queue_settings: dict[str, Any] = {
            "check_interval_seconds": settings.check_interval_seconds,
            "max_queue_size": settings.max_queue_size,
            "auto_clear_after_days": settings.auto_clear_after_days,
            "calendar_gate_enabled": settings.calendar_gate_enabled,
            "avg_context_switch_cost_minutes": settings.avg_context_switch_cost_minutes,
        }
        self.stats: dict[str, Any] = {
            "interruptions_caught_today": 0,
            "releases_today": 0,
            "focus_minutes_protected_today": 0,
        }
        self.load()

    def add(self, notification: Notification) -> Notification:
        """Insert or update a notification, then persist."""
        existing = self.notifications.get(notification.id)
        if existing is not None:
            existing.update(notification)
            stored = existing
        else:
            self.notifications[notification.id] = notification
            stored = notification
        self._enforce_max_size()
        self.save()
        return stored

    def get_pending(self) -> list[Notification]:
        """Unread, non-deferred notifications, newest first."""
        pending = [
            n
            for n in self.notifications.values()
            if not n.read and not n.deferred
        ]
        pending.sort(key=lambda n: n.timestamp, reverse=True)
        return pending

    def get_queue_snapshot(self) -> list[dict[str, Any]]:
        """Plain-dict view of pending items for renderers."""
        return [n.to_dict() for n in self.get_pending()]

    def get_stats_snapshot(self) -> dict[str, Any]:
        """Plain-dict view of persisted stats for renderers."""
        return dict(self.stats)

    def load(self) -> None:
        """Load queue.json, repairing from backup if the file is corrupt."""
        if not self.file_path.exists():
            return
        try:
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            self._apply_loaded(raw)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            backup = self.file_path.with_suffix(".json.bak")
            try:
                shutil.copy2(self.file_path, backup)
                logger.exception("Corrupt queue at %s; backed up to %s", self.file_path, backup)
            except OSError:
                logger.exception("Corrupt queue at %s and backup failed", self.file_path)
            self.notifications = {}
            self.save()

    def save(self) -> None:
        """Write the TRD §5 queue-state document to disk."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "notifications": [n.to_dict() for n in self.notifications.values()],
            "settings": self.queue_settings,
            "stats": self.stats,
        }
        tmp = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.file_path)

    def _apply_loaded(self, raw: Any) -> None:
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("notifications") or []
            if isinstance(raw.get("settings"), dict):
                self.queue_settings.update(raw["settings"])
            if isinstance(raw.get("stats"), dict):
                self.stats.update(raw["stats"])
        else:
            raise ValueError("queue file must be an object or array")

        loaded: dict[str, Notification] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            notif = Notification.from_dict(item)
            loaded[notif.id] = notif
        self.notifications = loaded

    def _enforce_max_size(self) -> None:
        max_size = int(self.queue_settings.get("max_queue_size") or self.settings.max_queue_size)
        if max_size <= 0 or len(self.notifications) <= max_size:
            return
        overflow = len(self.notifications) - max_size
        removable = sorted(
            self.notifications.values(),
            key=lambda n: (not n.read and not n.deferred, n.timestamp),
        )
        for notif in removable[:overflow]:
            self.notifications.pop(notif.id, None)
