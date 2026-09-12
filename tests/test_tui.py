"""Interactive TUI command-loop tests."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue
from src.tui import TUI


def _settings(tmp: Path) -> Settings:
    base = Settings.load()
    return Settings(
        **{
            **base.__dict__,
            "queue_file_path": tmp / "queue.json",
            "focus_mode": False,
            "calendar_gate_enabled": True,
            "google_calendar_credentials_file": None,
        }
    )


def _notif(raw_id: int, title: str, **kwargs: object) -> Notification:
    return Notification(
        source="github",
        type="issue",
        author="Ada",
        title=title,
        url=f"https://example.com/issues/{raw_id}",
        raw_id=raw_id,
        summary=f"[Issue] Ada: {title}",
        **kwargs,
    )


class TuiSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"tui_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.dir)
        self.queue = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.queue.add(_notif(1, "Defer this", timestamp=200.0))
        self.queue.add(_notif(2, "Dismiss this", timestamp=100.0))
        self.buf = io.StringIO()
        self.tui = TUI(console=Console(file=self.buf, force_terminal=False, width=80))
        self.opened: list[str] = []

    def tearDown(self) -> None:
        import shutil

        if self.dir.exists():
            shutil.rmtree(self.dir)

    def test_defer_dismiss_open_then_quit(self) -> None:
        commands = iter(["d 1", "o 1", "x 1", "q"])
        self.tui.run_session(
            self.queue,
            stats_fn=self.queue.get_stats_snapshot,
            input_fn=lambda _prompt: next(commands),
            open_url=self.opened.append,
        )
        output = self.buf.getvalue()
        self.assertIn("Deferred item 1.", output)
        self.assertIn("Dismissed item 1.", output)
        self.assertIn("Opened https://example.com/issues/2", output)
        self.assertEqual(self.opened, ["https://example.com/issues/2"])
        self.assertEqual(self.queue.get_pending(), [])
        self.assertTrue(self.queue.notifications["github-issue-1"].deferred)
        self.assertTrue(self.queue.notifications["github-issue-2"].read)

    def test_bad_index_and_unknown_stay_in_queue(self) -> None:
        commands = iter(["z 1", "d 9", "q"])
        self.tui.run_session(
            self.queue,
            input_fn=lambda _prompt: next(commands),
            open_url=self.opened.append,
        )
        output = self.buf.getvalue()
        self.assertIn("Unknown command", output)
        self.assertIn("No item 9", output)
        self.assertEqual(len(self.queue.get_pending()), 2)


if __name__ == "__main__":
    unittest.main()
