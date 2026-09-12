"""GitHub webhook listener. Normalizes events into Notification objects."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Callable

from flask import Flask, request

from src.notification import Notification
from src.queue import NotificationQueue

logger = logging.getLogger(__name__)

SummarizeFn = Callable[[Notification], str]

_HANDLED_EVENTS = {"pull_request", "issues", "issue_comment"}


def normalize_github_event(event_type: str, payload: dict[str, Any]) -> Notification | None:
    """Turn a GitHub webhook payload into a Notification, or None if ignored."""
    if event_type == "pull_request":
        item = payload.get("pull_request") or {}
        return _from_item("pull_request", item, item)
    if event_type == "issues":
        item = payload.get("issue") or {}
        return _from_item("issue", item, item)
    if event_type == "issue_comment":
        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        title = issue.get("title") or (comment.get("body") or "")[:80]
        return _from_item("mention", comment, comment, title=title)
    return None


def create_app(
    queue: NotificationQueue,
    webhook_secret: str = "",
    summarize: SummarizeFn | None = None,
) -> Flask:
    """Build the Flask app that receives GitHub webhooks on /github/webhook."""
    app = Flask(__name__)

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.post("/github/webhook")
    def handle_webhook() -> tuple[dict[str, str], int]:
        raw = request.get_data()
        if not _signature_ok(webhook_secret, raw, request.headers.get("X-Hub-Signature-256")):
            logger.error("Rejected webhook: invalid signature")
            return {"error": "invalid signature"}, 401

        event_type = request.headers.get("X-GitHub-Event", "")
        if event_type == "ping":
            return {"status": "pong"}, 200

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            logger.exception("Rejected webhook: invalid JSON")
            return {"error": "invalid json"}, 400

        if event_type not in _HANDLED_EVENTS:
            return {"status": "ignored"}, 200

        try:
            notif = normalize_github_event(event_type, payload)
            if notif is None:
                return {"status": "ignored"}, 200
            if summarize is not None:
                notif.summary = summarize(notif)
            queue.add(notif)
            logger.info("Queued %s (%s)", notif.id, notif.title)
        except Exception:
            logger.exception("Failed to process %s webhook", event_type)
            return {"status": "error"}, 200

        return {"status": "ok"}, 200

    return app


def _from_item(
    notif_type: str,
    item: dict[str, Any],
    raw_data: dict[str, Any],
    title: str | None = None,
) -> Notification | None:
    raw_id = item.get("id")
    if raw_id is None:
        logger.error("GitHub payload missing id for type=%s", notif_type)
        return None
    user = item.get("user") or {}
    return Notification(
        source="github",
        type=notif_type,
        author=user.get("login") or "unknown",
        title=title if title is not None else (item.get("title") or ""),
        url=item.get("html_url") or "",
        raw_data=raw_data,
        raw_id=raw_id,
    )


def _signature_ok(secret: str, payload: bytes, header: str | None) -> bool:
    """Validate X-Hub-Signature-256; skip when no real secret is configured."""
    if not secret or secret == "replace-me":
        return True
    if not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, header)
