"""Rich table renderer for queued notifications. Styling lives in theme.py."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src import theme
from src.queue import NotificationQueue


class TUI:
    """Renders queue snapshots and applies stdin commands through the queue API."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def show_queue(
        self,
        notifications: list[dict[str, Any]],
        stats: dict[str, Any] | None = None,
        warning: str | None = None,
    ) -> None:
        """Print the focus-stat panel and the pending queue as a table."""
        self._print_header(stats)
        if warning:
            self.console.print(warning)
        if not notifications:
            self.console.print(theme.EMPTY_QUEUE_MESSAGE)
            return

        table = Table(title=theme.TABLE_TITLE_WITH_COUNT.format(count=len(notifications)))
        table.add_column(theme.COLUMN_INDEX, style=theme.COLUMN_INDEX_STYLE, width=4)
        table.add_column(theme.COLUMN_TYPE, width=14)
        table.add_column(theme.COLUMN_AUTHOR, width=16)
        table.add_column(theme.COLUMN_SUMMARY)

        for index, item in enumerate(notifications, start=1):
            summary = item.get("summary") or item.get("title") or ""
            table.add_row(
                str(index),
                item.get("type") or "",
                item.get("author") or "",
                summary,
                style=theme.row_style(item.get("urgency")),
            )

        self.console.print(table)

    def show_held(self, stats: dict[str, Any] | None = None, warning: str | None = None) -> None:
        """Tell the developer the queue exists but the meeting gate is holding it."""
        self._print_header(stats)
        if warning:
            self.console.print(warning)
        self.console.print(theme.HELD_MESSAGE)

    def run_session(
        self,
        queue: NotificationQueue,
        stats_fn: Callable[[], dict[str, Any]] | None = None,
        warning: str | None = None,
        *,
        input_fn: Callable[[str], str] = input,
        open_url: Callable[[str], object] | None = None,
    ) -> None:
        """Render the table, then loop on stdin until the user quits."""
        opener = webbrowser.open if open_url is None else open_url
        redraw = True
        items = queue.get_queue_snapshot()
        while True:
            if redraw:
                items = queue.get_queue_snapshot()
                stats = stats_fn() if stats_fn else None
                self.show_queue(items, stats, warning=warning)
                redraw = False
            self.console.print(theme.TUI_PROMPT)
            try:
                raw = input_fn(theme.TUI_INPUT)
            except EOFError:
                return
            if not raw.strip():
                continue
            action = self._apply_command(raw, items, queue, opener)
            if action == "quit":
                return
            if action == "redraw":
                redraw = True

    def _apply_command(
        self,
        raw: str,
        items: list[dict[str, Any]],
        queue: NotificationQueue,
        opener: Callable[[str], object],
    ) -> str:
        """Apply one command. Returns `quit`, `redraw`, or `continue`."""
        line = raw.strip()
        parts = line.split()
        cmd = parts[0].lower()
        if cmd in {"q", "quit", "exit"}:
            return "quit"
        if cmd not in {"d", "x", "o"}:
            self.console.print(theme.TUI_UNKNOWN)
            return "continue"
        if len(parts) != 2 or not parts[1].isdigit():
            self.console.print(theme.TUI_UNKNOWN)
            return "continue"
        index = int(parts[1])
        if index < 1 or index > len(items):
            self.console.print(theme.TUI_BAD_INDEX.format(n=index))
            return "continue"
        item = items[index - 1]
        if cmd == "d":
            queue.defer(str(item["id"]))
            self.console.print(theme.TUI_DEFERRED.format(n=index))
            return "redraw"
        if cmd == "x":
            queue.dismiss(str(item["id"]))
            self.console.print(theme.TUI_DISMISSED.format(n=index))
            return "redraw"
        url = str(item.get("url") or "")
        if not url:
            self.console.print(theme.TUI_NO_URL.format(n=index))
        else:
            opener(url)
            self.console.print(theme.TUI_OPENED.format(url=url))
        return "continue"

    def _print_header(self, stats: dict[str, Any] | None) -> None:
        minutes = 0.0
        if stats:
            minutes = float(stats.get("focus_minutes_protected_today") or 0)
        self.console.print(
            Panel(
                theme.FOCUS_STAT_TEMPLATE.format(minutes=minutes),
                title=theme.FOCUS_PANEL_TITLE,
            )
        )
