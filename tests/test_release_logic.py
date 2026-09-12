"""Release-logic tests for each trigger type."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue
from src.release_logic import ReleaseLogic


def _settings(tmp: Path, **overrides: object) -> Settings:
    base = Settings.load()
    values = {
        "github_webhook_host": base.github_webhook_host,
        "github_webhook_port": base.github_webhook_port,
        "github_webhook_secret": base.github_webhook_secret,
        "github_token": base.github_token,
        "ollama_url": base.ollama_url,
        "ollama_model": base.ollama_model,
        "ollama_timeout_seconds": base.ollama_timeout_seconds,
        "queue_file_path": tmp / "queue.json",
        "check_interval_seconds": base.check_interval_seconds,
        "git_commit_trigger": base.git_commit_trigger,
        "build_success_trigger": base.build_success_trigger,
        "manual_trigger_enabled": True,
        "max_queue_size": base.max_queue_size,
        "auto_clear_after_days": base.auto_clear_after_days,
        "calendar_gate_enabled": base.calendar_gate_enabled,
        "avg_context_switch_cost_minutes": base.avg_context_switch_cost_minutes,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class ReleaseLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"rel_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.dir)
        self.queue = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.logic = ReleaseLogic(self.queue, self.settings)

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def _seed(self) -> None:
        self.queue.add(
            Notification(
                source="github",
                type="issue",
                author="Bob",
                title="Deploy failed in prod",
                url="https://example.com/issues/1",
                raw_id=1,
                summary="[Issue] Bob: Deploy failed in prod",
            )
        )

    def test_manual_trigger_releases_pending(self) -> None:
        self._seed()
        self.assertTrue(self.logic.should_release(manual=True))
        snap = self.logic.manual_release()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["id"], "github-issue-1")

    def test_manual_trigger_empty_queue(self) -> None:
        self.assertFalse(self.logic.should_release(manual=True))
        self.assertEqual(self.logic.manual_release(), [])

    def test_commit_trigger_stubbed(self) -> None:
        self._seed()
        self.assertFalse(self.logic._git_commit_detected())
        self.assertFalse(self.logic.should_release(manual=False))

    def test_build_trigger_stubbed(self) -> None:
        self._seed()
        self.assertFalse(self.logic._build_success_detected())

    def test_timer_trigger_stubbed(self) -> None:
        self._seed()
        self.assertFalse(self.logic._timer_elapsed())

    def test_manual_disabled(self) -> None:
        self._seed()
        disabled = ReleaseLogic(self.queue, _settings(self.dir, manual_trigger_enabled=False))
        self.assertFalse(disabled.should_release(manual=True))

    def test_focus_mode_holds_release(self) -> None:
        self._seed()
        held = ReleaseLogic(self.queue, self.settings, focus_mode_override=True)
        self.assertTrue(held.is_held())
        self.assertFalse(held.should_release(manual=True))
        self.assertEqual(held.manual_release(), [])

    def test_focus_mode_off_releases(self) -> None:
        self._seed()
        open_gate = ReleaseLogic(self.queue, self.settings, focus_mode_override=False)
        self.assertFalse(open_gate.is_held())
        self.assertTrue(open_gate.should_release(manual=True))
        self.assertEqual(len(open_gate.manual_release()), 1)

    def test_release_increments_analytics(self) -> None:
        self._seed()
        self.assertEqual(self.queue.stats["interruptions_caught_today"], 1)
        self.logic.manual_release()
        self.assertEqual(self.queue.stats["releases_today"], 1)
        self.assertEqual(self.queue.stats["focus_minutes_protected_today"], 17.5)

    def test_held_release_does_not_increment_releases(self) -> None:
        self._seed()
        held = ReleaseLogic(self.queue, self.settings, focus_mode_override=True)
        held.manual_release()
        self.assertEqual(self.queue.stats["releases_today"], 0)

    def test_calendar_gate_disabled_ignores_focus_mode(self) -> None:
        self._seed()
        ungated = ReleaseLogic(
            self.queue,
            _settings(self.dir, calendar_gate_enabled=False),
            focus_mode_override=True,
        )
        self.assertTrue(ungated.should_release(manual=True))

    def test_persisted_focus_mode(self) -> None:
        self._seed()
        self.logic.set_focus_mode(True)
        again = ReleaseLogic(self.queue, self.settings)
        self.assertTrue(again.is_held())
        self.logic.set_focus_mode(False)
        self.assertFalse(ReleaseLogic(self.queue, self.settings).is_held())


if __name__ == "__main__":
    unittest.main()
