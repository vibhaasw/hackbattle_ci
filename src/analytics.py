"""Focus-minutes analytics persisted in the queue stats block (TRD §3.6)."""

from __future__ import annotations

import logging
from typing import Any

from src.queue import NotificationQueue

logger = logging.getLogger(__name__)


def on_capture(queue: NotificationQueue, *, persist: bool = True) -> None:
    """Increment interruptions_caught and refresh the focus-minutes estimate."""
    queue.stats["interruptions_caught_today"] = (
        int(queue.stats.get("interruptions_caught_today") or 0) + 1
    )
    refresh_focus_minutes(queue)
    if persist:
        queue.save()
    logger.info(
        "Interruption caught (%s today, %s min protected)",
        queue.stats["interruptions_caught_today"],
        queue.stats["focus_minutes_protected_today"],
    )


def on_release(queue: NotificationQueue, *, persist: bool = True) -> None:
    """Increment releases_today after a successful release."""
    queue.stats["releases_today"] = int(queue.stats.get("releases_today") or 0) + 1
    refresh_focus_minutes(queue)
    if persist:
        queue.save()
    logger.info("Release recorded (%s today)", queue.stats["releases_today"])


def refresh_focus_minutes(queue: NotificationQueue) -> None:
    """focus_minutes_protected = interruptions_caught × avg cost (TRD §3.6)."""
    caught = int(queue.stats.get("interruptions_caught_today") or 0)
    cost = float(
        queue.queue_settings.get("avg_context_switch_cost_minutes")
        or queue.settings.avg_context_switch_cost_minutes
    )
    queue.stats["focus_minutes_protected_today"] = compute_focus_minutes(caught, cost)


def compute_focus_minutes(interruptions_caught: int, avg_cost_minutes: float) -> float:
    """Estimated focus minutes protected for the current totals."""
    return interruptions_caught * avg_cost_minutes


def stats_snapshot(queue: NotificationQueue) -> dict[str, Any]:
    """Plain-dict stats for renderers."""
    refresh_focus_minutes(queue)
    return queue.get_stats_snapshot()
