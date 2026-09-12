"""Ollama writes a short summary only. Urgency is decided by src.urgency."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue

logger = logging.getLogger(__name__)

_TYPE_LABELS = {
    "pull_request": "PR",
    "issue": "Issue",
    "mention": "Mention",
    "dm": "DM",
}
_MAX_WORDS = 15


class Summarizer:
    """Fill summary via Ollama (or the offline formatter). Never sets urgency."""

    def __init__(self, settings: Settings, queue: NotificationQueue | None = None) -> None:
        self.settings = settings
        self.queue = queue
        self._cache: dict[str, str] = {}

    def summarize(self, notification: Notification) -> str:
        """Set summary, then route + tag with offline workflow/urgency code."""
        cached = self._cache.get(notification.id)
        if cached:
            notification.summary = cached
        else:
            summary = ""
            try:
                summary = self._call_ollama(notification)
            except Exception as exc:
                logger.error(
                    "Ollama unavailable for %s (%s); using fallback formatter",
                    notification.id,
                    exc.__class__.__name__,
                )
            if not summary:
                summary = self.fallback_summary(notification)
            notification.summary = _clip_words(summary)
            self._cache[notification.id] = notification.summary

        self._apply_routing(notification)
        return notification.summary or ""

    def fallback_summary(self, notification: Notification) -> str:
        """TRD §6 formatter used when the LLM is unavailable."""
        label = _TYPE_LABELS.get(notification.type, notification.type.upper() or "NOTE")
        return f"[{label}] {notification.author}: {notification.title}".strip()

    def _apply_routing(self, notification: Notification) -> None:
        from src.workflows import apply, ensure_workflows

        workflows = ensure_workflows(self.queue) if self.queue is not None else []
        if not workflows:
            from src.workflows import default_workflows

            workflows = default_workflows()
        apply(notification, workflows)

    def _call_ollama(self, notification: Notification) -> str:
        raw_text = ""
        if isinstance(notification.raw_data, dict):
            raw_text = str(
                notification.raw_data.get("text")
                or notification.raw_data.get("body")
                or ""
            )
        prompt = (
            "Write a ≤15 word summary of this developer notification. "
            "Return only the summary sentence. No JSON, no urgency, no labels.\n\n"
            f"source={notification.source} type={notification.type} "
            f"author={notification.author} title={notification.title} "
            f"body={raw_text or notification.title}\n"
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
            return ""
        text = str((response.json() or {}).get("response") or "").strip()
        return _summary_from_model_text(text)


def _summary_from_model_text(text: str) -> str:
    """Accept plain text, or a leftover {summary} object. Ignore any urgency key."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        try:
            import json

            data: Any = json.loads(match.group(0))
            if isinstance(data, dict) and data.get("summary"):
                return str(data["summary"]).strip()
        except json.JSONDecodeError:
            quoted = re.search(r'"summary"\s*:\s*"([^"]+)"', match.group(0))
            if quoted:
                return quoted.group(1).strip()
    line = stripped.splitlines()[0].strip().strip('"').strip("'")
    if line.startswith("{") or line.lower().startswith("```"):
        return ""
    return line


def _clip_words(text: str, limit: int = _MAX_WORDS) -> str:
    words = [part for part in (text or "").split() if part]
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit])
