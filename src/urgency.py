"""Offline urgency tagging. Profiles are real code — the LLM never sets this."""

from __future__ import annotations

from typing import Any

from src.notification import Notification

Urgency = str  # urgent | normal | low

PROFILE_IDS = ("shipping", "oncall", "review", "quiet")

PROFILE_BLURBS = {
    "shipping": "Ship mode — prod/failures jump the line; bots and merged PRs stay low.",
    "oncall": "On-call — Slack pings and incidents are urgent; everything else stays visible.",
    "review": "Review mode — PRs and issues are the work; chat is quiet unless it is prod.",
    "quiet": "Quiet — only explicit incidents. Everything else is low.",
}

_URGENT = (
    "urgent",
    "asap",
    "sev",
    "sev0",
    "sev1",
    "p0",
    "p1",
    "incident",
    "outage",
    "down",
    "paging",
    "pager",
    "blocker",
    "blocking",
    "blocked",
    "prod",
    "production",
    "timeout",
    "timed out",
    "security",
    "leak",
    "exposed",
    "revoke",
    "breach",
    "@here",
    "@channel",
    "failed",
    "failure",
    "failing",
    "broken",
    "crash",
    "critical",
)
_LOW = (
    "merged",
    "closed",
    "renovate",
    "dependabot",
    "weekly",
    "recap",
    "fyi",
    "nit",
    "chore",
    "typo",
    "docs",
    "readme",
)


def classify(notification: Notification, profile: str = "shipping") -> Urgency:
    """Return urgent|normal|low using only notification fields and a named profile."""
    key = (profile or "shipping").strip().lower()
    if key not in PROFILE_IDS:
        key = "shipping"
    blob = _blob(notification)
    if key == "oncall":
        return _oncall(notification, blob)
    if key == "review":
        return _review(notification, blob)
    if key == "quiet":
        return _quiet(blob)
    return _shipping(notification, blob)


def profiles() -> list[dict[str, str]]:
    """Dashboard copy for the profile picker."""
    return [{"id": pid, "blurb": PROFILE_BLURBS[pid]} for pid in PROFILE_IDS]


def _shipping(notification: Notification, blob: str) -> Urgency:
    if _has_any(blob, _URGENT):
        return "urgent"
    if _has_any(blob, _LOW) or _looks_bot(notification):
        return "low"
    return "normal"


def _oncall(notification: Notification, blob: str) -> Urgency:
    if _has_any(blob, _URGENT):
        return "urgent"
    if notification.source == "slack" and notification.type in {"mention", "dm"}:
        return "urgent"
    if _has_any(blob, _LOW):
        return "low"
    return "normal"


def _review(notification: Notification, blob: str) -> Urgency:
    if _has_any(blob, ("prod", "production", "incident", "outage", "security", "p0", "@channel")):
        return "urgent"
    if notification.type in {"pull_request", "issue"}:
        return "normal"
    if notification.source == "slack":
        return "low"
    if _has_any(blob, _LOW):
        return "low"
    return "normal"


def _quiet(blob: str) -> Urgency:
    hard = (
        "incident",
        "outage",
        "prod",
        "production",
        "p0",
        "security",
        "@channel",
        "sev",
    )
    if _has_any(blob, hard):
        return "urgent"
    return "low"


def _blob(notification: Notification) -> str:
    # Title/type/payload only — never the LLM summary, so the model cannot steer tags.
    parts = [notification.title, notification.type, notification.source]
    raw = notification.raw_data if isinstance(notification.raw_data, dict) else {}
    for key in ("text", "body", "title"):
        parts.append(str(raw.get(key) or ""))
    return " ".join(parts).lower()


def _has_any(blob: str, needles: tuple[str, ...]) -> bool:
    return any(needle in blob for needle in needles)


def _looks_bot(notification: Notification) -> bool:
    author = (notification.author or "").lower()
    return author.endswith("[bot]") or author.endswith("-bot") or "dependabot" in author or "renovate" in author


def classify_dict(item: dict[str, Any], profile: str) -> Urgency:
    """Tag a stored snapshot dict without rehydrating the full listener path."""
    from src.notification import Notification as Notif

    return classify(Notif.from_dict(item), profile)
