"""Context Interrupter CLI: webhook daemon and manual queue release."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.github_listener import create_app
from src.queue import NotificationQueue
from src.analytics import stats_snapshot
from src.release_logic import ReleaseLogic
from src.slack_listener import run_socket_mode, start_slack_listener
from src.summarizer import Summarizer
from src.tui import TUI

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """CLI: `daemon` listens; `release` shows the TUI; `focus` toggles the meeting gate."""
    parser = argparse.ArgumentParser(description="Context Interrupter")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="Run the GitHub webhook listener")
    release = sub.add_parser("release", help="Manually release the queue into the TUI")
    release.add_argument(
        "--focus-mode",
        choices=("on", "off"),
        help="Override the meeting gate for this release only",
    )
    focus = sub.add_parser("focus", help="Persist the meeting / focus-mode gate")
    focus.add_argument("state", choices=("on", "off"))
    sub.add_parser("replay", help="Load canned demo/test_notifications.json into the queue")
    sub.add_parser("watch", help="Watch .git/HEAD and release the queue after a commit")
    return parser


def run_daemon(settings: Settings, queue: NotificationQueue) -> None:
    """Serve GitHub webhooks and, if configured, Slack Socket Mode."""
    summarizer = Summarizer(settings)
    handler = start_slack_listener(queue, settings, summarizer.summarize)
    if handler is not None:
        thread = threading.Thread(
            target=_run_slack,
            args=(handler,),
            name="slack-socket-mode",
            daemon=True,
        )
        thread.start()
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


def _run_slack(handler: object) -> None:
    """Block on Socket Mode; log and exit the thread if Slack drops."""
    try:
        run_socket_mode(handler)  # type: ignore[arg-type]
    except Exception:
        logger.exception(
            "Slack Socket Mode failed to start. "
            "GitHub listener keeps running. Check SLACK_BOT_TOKEN / SLACK_APP_TOKEN."
        )


def run_release(
    settings: Settings,
    queue: NotificationQueue,
    *,
    focus_mode: str | None = None,
) -> None:
    """Manual trigger: render whatever is currently pending, unless the gate holds."""
    override = None if focus_mode is None else focus_mode == "on"
    logic = ReleaseLogic(queue, settings, focus_mode_override=override)
    stats = stats_snapshot(queue)
    tui = TUI()
    if logic.is_held():
        tui.show_held(stats, warning=logic.gate_warning)
        return
    items = logic.manual_release()
    if not items:
        tui.show_queue([], stats_snapshot(queue), warning=logic.gate_warning)
        return
    tui.run_session(queue, lambda: stats_snapshot(queue), warning=logic.gate_warning)


def run_replay(queue: NotificationQueue) -> None:
    """Load canned GitHub + Slack notifications for a live-source outage."""
    import json

    from src.notification import Notification

    path = ROOT / "demo" / "test_notifications.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("notifications") or []
    loaded = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        queue.add(Notification.from_dict(item))
        loaded += 1
    logger.info("Replayed %s canned notification(s) from %s", loaded, path)


def run_watch(settings: Settings, queue: NotificationQueue) -> None:
    """Poll .git/HEAD and open the TUI when a new commit is detected."""
    logic = ReleaseLogic(queue, settings, git_root=ROOT)
    logic._git_commit_detected()
    logger.info("Watching %s for commits (Ctrl+C to stop)", ROOT / ".git" / "HEAD")
    try:
        while True:
            items = logic.auto_release()
            if items:
                TUI().run_session(
                    queue,
                    lambda: stats_snapshot(queue),
                    warning=logic.gate_warning,
                )
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped watching for commits")


def run_focus(settings: Settings, queue: NotificationQueue, state: str) -> None:
    """Turn the persisted meeting / focus-mode gate on or off."""
    ReleaseLogic(queue, settings).set_focus_mode(state == "on")


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
        run_release(settings, queue, focus_mode=getattr(args, "focus_mode", None))
    elif args.command == "focus":
        run_focus(settings, queue, args.state)
    elif args.command == "replay":
        run_replay(queue)
    elif args.command == "watch":
        run_watch(settings, queue)


if __name__ == "__main__":
    main()
