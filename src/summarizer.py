"""Ollama summarizer with urgency scoring (TRD §3.3)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

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
_VALID_URGENCY = {"urgent", "normal", "low"}
_URGENT_KEYWORDS = (
    "urgent",
    "breaking",
    "timeout",
    "blocking",
    "prod",
    "@here",
    "@channel",
)


class Summarizer:
    """Produce a <=15-word summary and an urgency tag via one Ollama call."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, tuple[str, str]] = {}

    def summarize(self, notification: Notification) -> str:
        """Set summary + urgency on the notification; return the summary text."""
        cached = self._cache.get(notification.id)
        if cached:
            notification.summary, notification.urgency = cached
            return cached[0]

        parsed: dict[str, Any] | None = None
        try:
            parsed = self._call_ollama(notification)
        except Exception as exc:
            logger.error(
                "Ollama unavailable for %s (%s); using fallback formatter",
                notification.id,
                exc.__class__.__name__,
            )

        summary = ""
        urgency = ""
        if parsed:
            summary = str(parsed.get("summary") or "").strip()
            tag = str(parsed.get("urgency") or "").strip().lower()
            if tag in _VALID_URGENCY:
                urgency = tag

        if not summary:
            summary = self.fallback_summary(notification)
        if not urgency:
            urgency = self.fallback_urgency(notification)

        notification.summary = summary
        notification.urgency = urgency
        self._cache[notification.id] = (summary, urgency)
        return summary

    def fallback_summary(self, notification: Notification) -> str:
        """TRD §6 formatter used when the LLM is unavailable."""
        label = _TYPE_LABELS.get(notification.type, notification.type.upper() or "NOTE")
        return f"[{label}] {notification.author}: {notification.title}".strip()

    def fallback_urgency(self, notification: Notification) -> str:
        """Keyword heuristic when the LLM is down or returns bad JSON (TRD §3.3)."""
        parts = [notification.title, notification.summary or ""]
        if isinstance(notification.raw_data, dict):
            parts.append(str(notification.raw_data.get("text") or ""))
            parts.append(str(notification.raw_data.get("body") or ""))
        blob = " ".join(parts).lower()
        for keyword in _URGENT_KEYWORDS:
            if keyword in blob:
                return "urgent"
        return "normal"

    def _call_ollama(self, notification: Notification) -> dict[str, Any] | None:
        body = notification.title
        raw_text = ""
        if isinstance(notification.raw_data, dict):
            raw_text = str(
                notification.raw_data.get("text")
                or notification.raw_data.get("body")
                or ""
            )
        prompt = (
            "Summarize this notification in ≤15 words and classify urgency.\n"
            'Return JSON: {"summary": "...", "urgency": "urgent|normal|low"}\n\n'
            f"Notification: source={notification.source}, "
            f"author={notification.author}, title={notification.title}, "
            f"body={raw_text or body}\n"
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
        parsed = _extract_json(text)
        if parsed is None:
            logger.error("Ollama returned non-JSON for %s: %s", notification.id, text[:200])
        return parsed


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output, including fenced snippets."""
    candidates = [text.strip()]
    fenced = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
