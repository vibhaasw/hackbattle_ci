"""Slack Socket Mode listener. Normalizes events into Notification objects."""

from __future__ import annotations

import logging
from threading import Event
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
    # Ignore bot DMs and message edits. Allow bot-authored app_mentions so a
    # bot can @itself in a test channel and still hit the real event path.
    if event.get("subtype"):
        return None
    if event.get("bot_id") and notif_type != "mention":
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


def slack_token_errors(bot_token: str, app_token: str) -> list[str]:
    """Return problems with Slack tokens (empty if both look valid)."""
    problems: list[str] = []
    for name, token, prefix in (
        ("SLACK_BOT_TOKEN", bot_token, "xoxb-"),
        ("SLACK_APP_TOKEN", app_token, "xapp-"),
    ):
        if not token:
            problems.append(f"{name} is missing")
        elif "replace-me" in token:
            problems.append(f"{name} is still a placeholder (replace-me)")
        elif not token.startswith(prefix):
            problems.append(f"{name} must start with {prefix}")
    return problems


def start_slack_listener(
    queue: NotificationQueue,
    settings: Settings,
    summarize: SummarizeFn | None = None,
) -> SocketModeHandler | None:
    """Build a Socket Mode handler, or None when Slack tokens are not configured."""
    problems = slack_token_errors(settings.slack_bot_token, settings.slack_app_token)
    if problems:
        for problem in problems:
            logger.error("Slack listener disabled: %s", problem)
        return None

    logger.info("Slack tokens loaded from environment; starting Socket Mode")
    # Default Bolt setting drops the bot's own posts, which hides self-@mentions
    # used by the smoke test and any other bot-authored mention.
    app = App(token=settings.slack_bot_token, ignoring_self_events_enabled=False)
    bot_user_id = ""
    try:
        bot_user_id = str(app.client.auth_test().get("user_id") or "")
    except Exception:
        logger.exception("Could not resolve Slack bot user id")

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel_type") == "im":
            ingest_slack_event(
                event, notif_type="dm", queue=queue, summarize=summarize, client=client
            )
            return
        # Bot self-@mention does not emit app_mention; catch it on the message event.
        text = event.get("text") or ""
        if (
            event.get("bot_id")
            and bot_user_id
            and f"<@{bot_user_id}>" in text
        ):
            ingest_slack_event(
                event, notif_type="mention", queue=queue, summarize=summarize, client=client
            )

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], client: Any) -> None:
        ingest_slack_event(
            event, notif_type="mention", queue=queue, summarize=summarize, client=client
        )

    return SocketModeHandler(app, settings.slack_app_token)


def run_socket_mode(handler: SocketModeHandler) -> None:
    """Connect, log a clear live/failed line, then block this thread."""
    handler.connect()
    if handler.client.is_connected():
        logger.info("Slack Socket Mode listener configured")
    else:
        logger.error(
            "Slack Socket Mode did not connect. "
            "Check SLACK_APP_TOKEN, that Socket Mode is enabled, and the app is installed."
        )
        return
    Event().wait()


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
