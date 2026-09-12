"""Ollama summarizer. Phase 1: one-line summary only, no urgency scoring."""

from __future__ import annotations

import logging

import requests

from src.config import Settings
from src.notification import Notification

logger = logging.getLogger(__name__)

_TYPE_LABELS = {
    "pull_request": "PR",
    "issue": "Issue",
    "mention": "Mention",
    "dm": "DM",
}


class Summarizer:
    """Produce a <=15-word summary, with a deterministic fallback if Ollama fails."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, str] = {}

    def summarize(self, notification: Notification) -> str:
        """Return a cached or freshly generated one-line summary."""
        cached = self._cache.get(notification.id)
        if cached:
            return cached

        try:
            summary = self._call_ollama(notification)
        except Exception:
            logger.exception("Ollama summarization failed for %s", notification.id)
            summary = None

        if not summary:
            summary = self.fallback_summary(notification)

        self._cache[notification.id] = summary
        return summary

    def fallback_summary(self, notification: Notification) -> str:
        """TRD §6 formatter used when the LLM is unavailable."""
        label = _TYPE_LABELS.get(notification.type, notification.type.upper() or "NOTE")
        return f"[{label}] {notification.author}: {notification.title}".strip()

    def _call_ollama(self, notification: Notification) -> str | None:
        prompt = (
            "You are a notification summarizer for developers.\n"
            "Summarize this notification in ONE line (max 15 words).\n"
            "Format: [TYPE] AUTHOR: ACTION (brief context)\n"
            "Examples:\n"
            "[PR] Alice: Auth service ready (3 files, +120 -45)\n"
            "[Issue] Bob: Deploy failed in prod (urgent)\n"
            "[Mention] You were mentioned in #backend thread\n\n"
            f"Type: {notification.type}\n"
            f"Author: {notification.author}\n"
            f"Title: {notification.title}\n\n"
            "Summary:"
        )
        response = requests.post(
            f"{self.settings.ollama_url}/api/generate",
            json={
                "model": self.settings.ollama_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=self.settings.ollama_timeout_seconds,
        )
        if response.status_code != 200:
            logger.error(
                "Ollama returned HTTP %s for %s",
                response.status_code,
                notification.id,
            )
            return None
        text = (response.json().get("response") or "").strip()
        return text or None
