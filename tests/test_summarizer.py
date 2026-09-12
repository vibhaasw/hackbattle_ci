"""Summarizer happy-path and Ollama-down fallback tests."""

from __future__ import annotations

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

    def test_happy_path_uses_ollama_response(self) -> None:
        fake = _FakeResponse(200, "[PR] Sarah: OAuth2 auth service (3 files)")
        with patch("src.summarizer.requests.post", return_value=fake) as mocked:
            summary = self.summarizer.summarize(self.notif)
        self.assertEqual(summary, "[PR] Sarah: OAuth2 auth service (3 files)")
        mocked.assert_called_once()

    def test_fallback_when_ollama_down(self) -> None:
        with patch("src.summarizer.requests.post", side_effect=ConnectionError("down")):
            summary = self.summarizer.summarize(self.notif)
        self.assertEqual(summary, "[PR] Sarah: Add OAuth2 support")

    def test_caches_by_notification_id(self) -> None:
        fake = _FakeResponse(200, "[PR] Sarah: cached")
        with patch("src.summarizer.requests.post", return_value=fake) as mocked:
            first = self.summarizer.summarize(self.notif)
            second = self.summarizer.summarize(self.notif)
        self.assertEqual(first, second)
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
