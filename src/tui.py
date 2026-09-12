"""Rich table renderer for queued notifications. Styling lives in theme.py."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src import theme


class TUI:
    """Pure renderer — reads snapshot dicts, never mutates queue state."""

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
