"""Queue dedup, persistence, and snapshot tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue


def _settings(tmp_path: Path) -> Settings:
    base = Settings.load()
    return Settings(
        github_webhook_host=base.github_webhook_host,
        github_webhook_port=base.github_webhook_port,
        github_webhook_secret=base.github_webhook_secret,
        github_token=base.github_token,
        ollama_url=base.ollama_url,
        ollama_model=base.ollama_model,
        ollama_timeout_seconds=base.ollama_timeout_seconds,
        queue_file_path=tmp_path / "queue.json",
        check_interval_seconds=base.check_interval_seconds,
        git_commit_trigger=base.git_commit_trigger,
        build_success_trigger=base.build_success_trigger,
        manual_trigger_enabled=base.manual_trigger_enabled,
        max_queue_size=50,
        auto_clear_after_days=base.auto_clear_after_days,
        calendar_gate_enabled=base.calendar_gate_enabled,
        avg_context_switch_cost_minutes=base.avg_context_switch_cost_minutes,
    )


def _notif(raw_id: int, title: str = "Add OAuth2 support", **kwargs: object) -> Notification:
    return Notification(
        source="github",
        type="pull_request",
        author="Sarah",
        title=title,
        url=f"https://example.com/pr/{raw_id}",
        raw_id=raw_id,
        **kwargs,
    )


class QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.id().replace(".", "_"))
        # isolate files under tests/.tmp
        self.dir = Path(__file__).resolve().parent / ".tmp" / str(id(self))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.dir)
        self.queue = NotificationQueue(self.settings.queue_file_path, self.settings)

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def test_add_and_snapshot(self) -> None:
        self.queue.add(_notif(456))
        snap = self.queue.get_queue_snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["id"], "github-pull_request-456")
        self.assertEqual(snap[0]["author"], "Sarah")

    def test_dedup_updates_existing(self) -> None:
        self.queue.add(_notif(456, title="Old title"))
        self.queue.add(_notif(456, title="New title"))
        pending = self.queue.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].title, "New title")

    def test_read_items_are_not_pending(self) -> None:
        n = _notif(1)
        n.read = True
        self.queue.add(n)
        self.assertEqual(self.queue.get_pending(), [])

    def test_persists_across_reload(self) -> None:
        self.queue.add(_notif(456, summary="[PR] Sarah: OAuth2"))
        reloaded = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.assertEqual(len(reloaded.notifications), 1)
        self.assertEqual(reloaded.get_pending()[0].summary, "[PR] Sarah: OAuth2")

    def test_empty_queue_on_startup(self) -> None:
        missing = self.dir / "brand-new.json"
        fresh = NotificationQueue(missing, self.settings)
        self.assertEqual(fresh.get_pending(), [])
        self.assertEqual(fresh.get_queue_snapshot(), [])
        self.assertEqual(fresh.stats["interruptions_caught_today"], 0)

    def test_long_title_persists(self) -> None:
        title = "x" * 5000
        self.queue.add(_notif(7, title=title))
        reloaded = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.assertEqual(len(reloaded.get_pending()[0].title), 5000)

    def test_capture_increments_interruptions(self) -> None:
        self.queue.add(_notif(1))
        self.queue.add(_notif(1, title="updated"))
        self.queue.add(_notif(2))
        self.assertEqual(self.queue.stats["interruptions_caught_today"], 2)
        self.assertEqual(self.queue.stats["focus_minutes_protected_today"], 2 * 17.5)

    def test_sorts_by_urgency_then_timestamp(self) -> None:
        self.queue.add(_notif(1, title="old normal", urgency="normal", timestamp=100.0))
        self.queue.add(_notif(2, title="new low", urgency="low", timestamp=300.0))
        self.queue.add(_notif(3, title="old urgent", urgency="urgent", timestamp=50.0))
        self.queue.add(_notif(4, title="new urgent", urgency="urgent", timestamp=200.0))
        titles = [n.title for n in self.queue.get_pending()]
        self.assertEqual(titles, ["new urgent", "old urgent", "old normal", "new low"])

    def test_repairs_corrupt_file(self) -> None:
        self.settings.queue_file_path.write_text("{not-json", encoding="utf-8")
        repaired = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.assertEqual(repaired.notifications, {})
        self.assertTrue(self.settings.queue_file_path.with_suffix(".json.bak").exists())
        stored = json.loads(self.settings.queue_file_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["notifications"], [])


if __name__ == "__main__":
    unittest.main()
