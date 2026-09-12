"""Slack Socket Mode listener. Normalizes events into Notification objects."""

from __future__ import annotations

import logging
from typing import Any, Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue

logger = logging.getLogger(__name__)

SummarizeFn = Callable[[Notification], str]


def normalize_slack_event(
    event: dict[str, Any],
    notif_type: str,
    author: str = "",
    permalink: str = "",
) -> Notification | None:
    """Turn a Slack event into a Notification, or None if it has no identity."""
    ts = event.get("ts")
    channel = event.get("channel") or ""
    team = event.get("team") or event.get("team_id") or "T"
    raw_id = event.get("client_msg_id") or (f"{team}-{channel}-{ts}" if ts else None)
    if raw_id is None:
        logger.error("Slack event missing id/ts")
        return None
    text = (event.get("text") or "").strip()
    return Notification(
        source="slack",
        type=notif_type,
        author=author or event.get("user") or "unknown",
        title=text[:160],
        url=permalink,
        raw_data=event,
        raw_id=raw_id,
    )


def ingest_slack_event(
    event: dict[str, Any],
    *,
    notif_type: str,
    queue: NotificationQueue,
    summarize: SummarizeFn | None = None,
    client: Any | None = None,
) -> Notification | None:
    """Normalize, summarize, and enqueue a Slack event. Never raises to the caller."""
    if event.get("bot_id") or event.get("subtype"):
        return None
    try:
        author = _resolve_author(client, event.get("user") or "")
        permalink = _resolve_permalink(
            client, event.get("channel") or "", event.get("ts") or ""
        )
        notif = normalize_slack_event(event, notif_type, author, permalink)
        if notif is None:
            return None
        if summarize is not None:
            notif.summary = summarize(notif)
        queue.add(notif)
        logger.info("Queued %s (%s)", notif.id, notif.title)
        return notif
    except Exception:
        logger.exception("Failed to process Slack %s event", notif_type)
        return None


def start_slack_listener(
    queue: NotificationQueue,
    settings: Settings,
    summarize: SummarizeFn | None = None,
) -> SocketModeHandler | None:
    """Build a Socket Mode handler, or None when Slack tokens are not configured."""
    if not _token_ready(settings.slack_bot_token, "xoxb-") or not _token_ready(
        settings.slack_app_token, "xapp-"
    ):
        logger.warning("Slack tokens missing; Socket Mode listener disabled")
        return None

    app = App(token=settings.slack_bot_token)

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel_type") != "im":
            return
        ingest_slack_event(
            event, notif_type="dm", queue=queue, summarize=summarize, client=client
        )

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], client: Any) -> None:
        ingest_slack_event(
            event, notif_type="mention", queue=queue, summarize=summarize, client=client
        )

    logger.info("Slack Socket Mode listener configured")
    return SocketModeHandler(app, settings.slack_app_token)


def _token_ready(token: str, prefix: str) -> bool:
    return bool(token) and token.startswith(prefix) and "replace-me" not in token


def _resolve_author(client: Any | None, user_id: str) -> str:
    if not user_id:
        return "unknown"
    if client is None:
        return user_id
    try:
        user = (client.users_info(user=user_id) or {}).get("user") or {}
        return user.get("real_name") or user.get("name") or user_id
    except Exception:
        logger.exception("Could not resolve Slack user %s", user_id)
        return user_id


def _resolve_permalink(client: Any | None, channel: str, ts: str) -> str:
    if client is None or not channel or not ts:
        return ""
    try:
        resp = client.chat_getPermalink(channel=channel, message_ts=ts) or {}
        return resp.get("permalink") or ""
    except Exception:
        logger.exception("Could not resolve Slack permalink for %s/%s", channel, ts)
        return ""
