"""Context Interrupter CLI: webhook daemon and manual queue release."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.github_listener import create_app
from src.queue import NotificationQueue
from src.release_logic import ReleaseLogic
from src.summarizer import Summarizer
from src.tui import TUI

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """CLI: `daemon` listens; `release` shows the TUI."""
    parser = argparse.ArgumentParser(description="Context Interrupter")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="Run the GitHub webhook listener")
    sub.add_parser("release", help="Manually release the queue into the TUI")
    return parser


def run_daemon(settings: Settings, queue: NotificationQueue) -> None:
    """Serve POST /github/webhook and persist summarized notifications."""
    summarizer = Summarizer(settings)
    app = create_app(queue, settings.github_webhook_secret, summarizer.summarize)
    logger.info(
        "Listening for GitHub webhooks on http://%s:%s/github/webhook",
        settings.github_webhook_host,
        settings.github_webhook_port,
    )
    app.run(
        host=settings.github_webhook_host,
        port=settings.github_webhook_port,
        use_reloader=False,
    )


def run_release(settings: Settings, queue: NotificationQueue) -> None:
    """Manual trigger: render whatever is currently pending."""
    items = ReleaseLogic(queue, settings).manual_release()
    TUI().show_queue(items)


def main() -> None:
    """Parse CLI args and start the requested command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    settings = Settings.load()
    queue = NotificationQueue(settings.queue_file_path, settings)
    if args.command == "daemon":
        run_daemon(settings, queue)
    elif args.command == "release":
        run_release(settings, queue)


if __name__ == "__main__":
    main()
