"""Source-agnostic notification model (TRD §5)."""

from __future__ import annotations

import time
from typing import Any


class Notification:
    """A single queued notification from any source."""

    def __init__(
        self,
        source: str,
        type: str,
        author: str,
        title: str,
        url: str,
        raw_data: dict[str, Any] | None = None,
        raw_id: str | int | None = None,
        summary: str | None = None,
        urgency: str = "normal",
        timestamp: float | None = None,
        read: bool = False,
        deferred: bool = False,
        id: str | None = None,
        workflow_id: str = "",
    ) -> None:
        data = raw_data or {}
        resolved_raw_id = raw_id if raw_id is not None else data.get("id")
        if id is not None:
            self.id = str(id)
        elif resolved_raw_id is None:
            raise ValueError("Notification requires id, raw_id, or raw_data['id']")
        else:
            self.id = f"{source}-{type}-{resolved_raw_id}"

        self.source = source
        self.type = type
        self.author = author or "unknown"
        self.title = title or ""
        self.url = url or ""
        self.raw_data = data
        self.summary = summary
        self.urgency = urgency or "normal"
        self.timestamp = time.time() if timestamp is None else float(timestamp)
        self.read = read
        self.deferred = deferred
        self.workflow_id = workflow_id or ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the TRD §5 JSON shape."""
        return {
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "author": self.author,
            "title": self.title,
            "summary": self.summary,
            "urgency": self.urgency,
            "url": self.url,
            "timestamp": self.timestamp,
            "read": self.read,
            "deferred": self.deferred,
            "workflow_id": self.workflow_id,
            "raw_data": self.raw_data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Notification:
        """Rehydrate a notification from stored JSON."""
        return cls(
            source=data.get("source", ""),
            type=data.get("type", ""),
            author=data.get("author", "unknown"),
            title=data.get("title", ""),
            url=data.get("url", ""),
            raw_data=data.get("raw_data") or {},
            summary=data.get("summary"),
            urgency=data.get("urgency", "normal"),
            timestamp=data.get("timestamp"),
            read=bool(data.get("read", False)),
            deferred=bool(data.get("deferred", False)),
            id=data.get("id"),
            workflow_id=str(data.get("workflow_id") or ""),
        )

    def update(self, incoming: Notification) -> None:
        """Merge a newer event with the same id, resurfacing it as unread."""
        self.author = incoming.author or self.author
        self.title = incoming.title or self.title
        self.url = incoming.url or self.url
        if incoming.raw_data:
            self.raw_data = incoming.raw_data
        self.timestamp = incoming.timestamp
        if incoming.summary:
            self.summary = incoming.summary
        if incoming.urgency:
            self.urgency = incoming.urgency
        if incoming.workflow_id:
            self.workflow_id = incoming.workflow_id
        self.read = False
        self.deferred = False
