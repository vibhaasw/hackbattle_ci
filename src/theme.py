"""Display strings and table labels. Safe for the design teammate to edit."""

TABLE_TITLE = "CONTEXT QUEUE"
TABLE_TITLE_WITH_COUNT = "CONTEXT QUEUE [{count} waiting]"
COLUMN_INDEX = "#"
COLUMN_TYPE = "Type"
COLUMN_AUTHOR = "Author"
COLUMN_SUMMARY = "Summary"
COLUMN_INDEX_STYLE = "bold"
EMPTY_QUEUE_MESSAGE = "No pending notifications."
HELD_MESSAGE = "Queue held — focus mode is on (simulated meeting)."

# Urgency row styles — edit here, not in tui.py
URGENCY_STYLE = {
    "urgent": "bold red",
    "normal": "",
    "low": "dim",
}


FOCUS_PANEL_TITLE = "FOCUS"
FOCUS_STAT_TEMPLATE = "🛡 {minutes:g} min protected today"


def row_style(urgency: str | None) -> str:
    """Rich style name for a notification row, keyed by urgency."""
    key = (urgency or "normal").strip().lower()
    return URGENCY_STYLE.get(key, URGENCY_STYLE["normal"])

