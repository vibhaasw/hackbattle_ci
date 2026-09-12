"""Summarizer happy-path JSON parsing and Ollama-down fallback tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.notification import Notification
from src.summarizer import Summarizer


def _settings() -> Settings:
    return Settings.load()


def _notif() -> Notification:
    return Notification(
        source="github",
        type="pull_request",
        author="Sarah",
        title="Add OAuth2 support",
        url="https://example.com/pr/456",
        raw_id=456,
    )


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self._text = text

    def json(self) -> dict[str, str]:
        return {"response": self._text}


class SummarizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summarizer = Summarizer(_settings())
        self.notif = _notif()

    def test_happy_path_parses_json(self) -> None:
        payload = json.dumps(
            {
                "summary": "[PR] Sarah: OAuth2 auth service (3 files)",
                "urgency": "normal",
            }
        )
        fake = _FakeResponse(200, payload)
        with patch("src.summarizer.requests.post", return_value=fake) as mocked:
            summary = self.summarizer.summarize(self.notif)
        self.assertEqual(summary, "[PR] Sarah: OAuth2 auth service (3 files)")
        self.assertEqual(self.notif.urgency, "normal")
        mocked.assert_called_once()

    def test_parses_fenced_json_and_urgent_tag(self) -> None:
        fake = _FakeResponse(
            200,
            'Sure.\n```json\n{"summary": "[Issue] Bob: deploy down", "urgency": "URGENT"}\n```',
        )
        with patch("src.summarizer.requests.post", return_value=fake):
            self.summarizer.summarize(self.notif)
        self.assertEqual(self.notif.summary, "[Issue] Bob: deploy down")
        self.assertEqual(self.notif.urgency, "urgent")

    def test_fallback_when_ollama_down(self) -> None:
        with patch("src.summarizer.requests.post", side_effect=ConnectionError("down")):
            summary = self.summarizer.summarize(self.notif)
        self.assertEqual(summary, "[PR] Sarah: Add OAuth2 support")
        self.assertEqual(self.notif.urgency, "normal")

    def test_malformed_json_uses_fallback_summary(self) -> None:
        fake = _FakeResponse(200, "this is not json at all")
        with patch("src.summarizer.requests.post", return_value=fake):
            summary = self.summarizer.summarize(self.notif)
        self.assertEqual(summary, "[PR] Sarah: Add OAuth2 support")
        self.assertEqual(self.notif.urgency, "normal")

    def test_keyword_fallback_marks_urgent(self) -> None:
        self.notif.title = "Deploy failed in prod (timeout)"
        with patch("src.summarizer.requests.post", side_effect=ConnectionError("down")):
            self.summarizer.summarize(self.notif)
        self.assertEqual(self.notif.urgency, "urgent")
        self.assertIn("Deploy failed in prod", self.notif.summary)

    def test_keyword_fallback_from_slack_channel_ping(self) -> None:
        slack = Notification(
            source="slack",
            type="mention",
            author="Riley",
            title="please look",
            url="",
            raw_id="1",
            raw_data={"text": "<@U999> build is blocking @channel"},
        )
        fake = _FakeResponse(200, "not-json")
        with patch("src.summarizer.requests.post", return_value=fake):
            self.summarizer.summarize(slack)
        self.assertEqual(slack.urgency, "urgent")

    def test_caches_by_notification_id(self) -> None:
        payload = json.dumps({"summary": "[PR] Sarah: cached", "urgency": "low"})
        fake = _FakeResponse(200, payload)
        with patch("src.summarizer.requests.post", return_value=fake) as mocked:
            first = self.summarizer.summarize(self.notif)
            second = self.summarizer.summarize(self.notif)
        self.assertEqual(first, second)
        self.assertEqual(self.notif.urgency, "low")
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
