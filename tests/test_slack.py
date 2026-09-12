"""Slack listener tests using canned Socket Mode event payloads."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.queue import NotificationQueue
from src.slack_listener import ingest_slack_event, normalize_slack_event, slack_token_errors

DM_EVENT = {
    "type": "message",
    "channel_type": "im",
    "channel": "D012345",
    "user": "U111",
    "text": "Can you review the deploy?",
    "ts": "1710000000.000100",
    "team": "T123",
    "client_msg_id": "11111111-aaaa-bbbb-cccc-222222222222",
}

MENTION_EVENT = {
    "type": "app_mention",
    "channel": "C456",
    "user": "U222",
    "text": "<@U999> prod is timing out @channel",
    "ts": "1710000001.000200",
    "team": "T123",
    "client_msg_id": "33333333-aaaa-bbbb-cccc-444444444444",
}


def _settings(tmp: Path) -> Settings:
    base = Settings.load()
    return Settings(
        github_webhook_host=base.github_webhook_host,
        github_webhook_port=base.github_webhook_port,
        github_webhook_secret=base.github_webhook_secret,
        github_token=base.github_token,
        ollama_url=base.ollama_url,
        ollama_model=base.ollama_model,
        ollama_timeout_seconds=base.ollama_timeout_seconds,
        queue_file_path=tmp / "queue.json",
        check_interval_seconds=base.check_interval_seconds,
        git_commit_trigger=base.git_commit_trigger,
        build_success_trigger=base.build_success_trigger,
        manual_trigger_enabled=base.manual_trigger_enabled,
        max_queue_size=base.max_queue_size,
        auto_clear_after_days=base.auto_clear_after_days,
        calendar_gate_enabled=base.calendar_gate_enabled,
        avg_context_switch_cost_minutes=base.avg_context_switch_cost_minutes,
    )


class SlackListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"sl_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.queue = NotificationQueue(_settings(self.dir).queue_file_path, _settings(self.dir))

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def test_normalize_dm(self) -> None:
        notif = normalize_slack_event(DM_EVENT, "dm", author="Sam", permalink="https://slack/dm")
        assert notif is not None
        self.assertEqual(notif.source, "slack")
        self.assertEqual(notif.type, "dm")
        self.assertEqual(notif.author, "Sam")
        self.assertIn("review the deploy", notif.title)

    def test_normalize_mention(self) -> None:
        notif = normalize_slack_event(MENTION_EVENT, "mention", author="Riley")
        assert notif is not None
        self.assertEqual(notif.type, "mention")
        self.assertTrue(notif.id.startswith("slack-mention-"))

    def test_ingest_queues_dm(self) -> None:
        notif = ingest_slack_event(DM_EVENT, notif_type="dm", queue=self.queue)
        assert notif is not None
        pending = self.queue.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].source, "slack")
        self.assertEqual(pending[0].type, "dm")

    def test_ignores_bot_and_subtype(self) -> None:
        bot = dict(DM_EVENT, bot_id="B01")
        edited = dict(DM_EVENT, subtype="message_changed")
        self.assertIsNone(ingest_slack_event(bot, notif_type="dm", queue=self.queue))
        self.assertIsNone(ingest_slack_event(edited, notif_type="dm", queue=self.queue))
        self.assertEqual(self.queue.get_pending(), [])

    def test_summarize_callback(self) -> None:
        ingest_slack_event(
            MENTION_EVENT,
            notif_type="mention",
            queue=self.queue,
            summarize=lambda n: f"SUM:{n.title[:20]}",
        )
        self.assertTrue(self.queue.get_pending()[0].summary.startswith("SUM:"))

    def test_settings_reads_slack_tokens_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test-token-value",
                "SLACK_APP_TOKEN": "xapp-test-token-value",
            },
        ):
            settings = Settings.load()
        self.assertEqual(settings.slack_bot_token, "xoxb-test-token-value")
        self.assertEqual(settings.slack_app_token, "xapp-test-token-value")

    def test_missing_and_placeholder_tokens_are_explicit(self) -> None:
        missing = slack_token_errors("", "")
        self.assertTrue(any("SLACK_BOT_TOKEN is missing" in e for e in missing))
        self.assertTrue(any("SLACK_APP_TOKEN is missing" in e for e in missing))
        placeholders = slack_token_errors("xoxb-replace-me", "xapp-replace-me")
        self.assertTrue(any("placeholder" in e for e in placeholders))
        self.assertEqual(slack_token_errors("xoxb-live-token", "xapp-live-token"), [])


if __name__ == "__main__":
    unittest.main()
