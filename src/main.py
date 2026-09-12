"""Context Interrupter CLI: capture listeners, release triggers, and TUI."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api import attach_runtime_routes
from src.config import Settings
from src.github_listener import create_app
from src.queue import NotificationQueue
from src.analytics import reset_stats, stats_snapshot
from src.release_logic import ReleaseLogic
from src.slack_listener import run_socket_mode, start_slack_listener
from src.summarizer import Summarizer
from src.tui import TUI

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """CLI: `daemon` listens; `release` shows the TUI; `focus` toggles the meeting gate."""
    parser = argparse.ArgumentParser(description="Context Interrupter")
    sub = parser.add_subparsers(dest="command", required=True)
    daemon = sub.add_parser(
        "daemon",
        help="Same as watch: GitHub + Slack listeners and commit/timer release",
    )
    daemon.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        metavar="N",
        help="Override check_interval_seconds (default: queue setting, 3600)",
    )
    release = sub.add_parser("release", help="Manually release the queue into the TUI")
    release.add_argument(
        "--focus-mode",
        choices=("on", "off"),
        help="Override the meeting gate for this release only",
    )
    focus = sub.add_parser("focus", help="Persist the meeting / focus-mode gate")
    focus.add_argument("state", choices=("on", "off"))
    sub.add_parser("replay", help="Load canned demo/test_notifications.json into the queue")
    watch = sub.add_parser(
        "watch",
        help="GitHub webhook + Slack Socket Mode + commit/timer release (demo entry point)",
    )
    watch.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        metavar="N",
        help="Override check_interval_seconds for this watch (default: queue setting, 3600)",
    )
    sub.add_parser(
        "reset-stats",
        help="Zero today's demo counters in queue.json (notifications stay queued)",
    )
    sub.add_parser(
        "setup",
        help="Interactive wizard: create the GitHub webhook and write Slack tokens into .env",
    )
    sub.add_parser(
        "start",
        help="One command: ngrok + webhook sync + listeners + dashboard API",
    )
    sub.add_parser(
        "prime",
        help="Empty the pending queue and zero today's stats for a live judge demo",
    )
    return parser


def run_daemon(
    settings: Settings,
    queue: NotificationQueue,
    *,
    interval_seconds: int | None = None,
) -> None:
    """Alias for watch — same process runs capture listeners and release triggers."""
    run_watch(settings, queue, interval_seconds=interval_seconds)


def _start_capture_listeners(settings: Settings, queue: NotificationQueue) -> None:
    """Start Slack Socket Mode and the GitHub Flask listener in background threads."""
    summarizer = Summarizer(settings, queue)
    handler = start_slack_listener(queue, settings, summarizer.summarize)
    if handler is not None:
        threading.Thread(
            target=_run_slack,
            args=(handler,),
            name="slack-socket-mode",
            daemon=True,
        ).start()
        time.sleep(2.5)
    app = create_app(queue, settings.github_webhook_secret, summarizer.summarize)
    attach_runtime_routes(app, queue, settings)
    logger.info(
        "Listening for GitHub webhooks on http://%s:%s/github/webhook",
        settings.github_webhook_host,
        settings.github_webhook_port,
    )
    logger.info(
        "Dashboard + API on http://127.0.0.1:%s/",
        settings.github_webhook_port,
    )
    thread = threading.Thread(
        target=_run_flask,
        args=(app, settings),
        name="github-webhook",
        daemon=True,
    )
    thread.start()
    _wait_for_port(settings.github_webhook_host, settings.github_webhook_port)


def _run_flask(app: object, settings: Settings) -> None:
    """Block this thread on the GitHub webhook server."""
    app.run(  # type: ignore[attr-defined]
        host=settings.github_webhook_host,
        port=settings.github_webhook_port,
        use_reloader=False,
        threaded=True,
    )


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until Flask is accepting connections (or the timeout expires)."""
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    deadline = time.time() + timeout
    last_exc: OSError | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.3):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.1)
    logger.warning("GitHub webhook port %s:%s not open yet (%s)", probe_host, port, last_exc)


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


def run_watch(
    settings: Settings,
    queue: NotificationQueue,
    *,
    interval_seconds: int | None = None,
) -> None:
    """Capture listeners plus git/timer release, all in one process."""
    _start_capture_listeners(settings, queue)
    logic = ReleaseLogic(
        queue, settings, git_root=ROOT, interval_seconds=interval_seconds
    )
    logic._git_commit_detected()
    interval = logic._interval_seconds()
    logger.info(
        "Watching %s for commits and a %ss timer (Ctrl+C to stop)",
        ROOT / ".git" / "HEAD",
        interval,
    )
    try:
        while True:
            queue.load()
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


def run_setup_command() -> None:
    """Onboard GitHub + Slack, write .env, optionally start watch."""
    from dotenv import load_dotenv

    from src.setup_wizard import run_setup

    result = run_setup(env_path=ROOT / ".env")
    if not result.start_watch:
        return
    load_dotenv(ROOT / ".env", override=True)
    settings = Settings.load()
    queue = NotificationQueue(settings.queue_file_path, settings)
    run_watch(settings, queue)


def run_prime(queue: NotificationQueue) -> None:
    """Clear leftover test items and zero counters so the dashboard starts empty."""
    queue.load()
    pending_before = [n.id for n in queue.get_pending()]
    cleared = queue.dismiss_all_pending()
    previous = reset_stats(queue)
    print(f"Primed {queue.file_path} for a live demo")
    print(f"  dismissed {cleared} pending item(s)")
    if pending_before:
        print("  was: " + ", ".join(pending_before))
    print(
        f"  interruptions_caught_today: {previous['interruptions_caught_today']} → 0"
    )
    print(f"  releases_today: {previous['releases_today']} → 0")
    print(
        f"  focus_minutes_protected_today: "
        f"{previous['focus_minutes_protected_today']} → 0"
    )
    print("Dashboard panes stay empty until a real GitHub or Slack event arrives.")
    print("Next: keep `python -m src.main start` running, then:")
    print("  .venv/bin/python scripts/live_demo.py")
    print("  or @mention the Slack bot / open a GitHub issue on the connected repo.")


def run_reset_stats(queue: NotificationQueue) -> None:
    """Zero daily counters and print the previous values for a sanity check."""
    previous = reset_stats(queue)
    print(f"Reset daily stats in {queue.file_path}")
    print(
        f"  interruptions_caught_today: "
        f"{previous['interruptions_caught_today']} → 0"
    )
    print(f"  releases_today: {previous['releases_today']} → 0")
    print(
        f"  focus_minutes_protected_today: "
        f"{previous['focus_minutes_protected_today']} → 0"
    )
    print(
        f"Notifications left untouched ({len(queue.notifications)} item(s))."
    )


def main() -> None:
    """Parse CLI args and start the requested command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    if args.command == "setup":
        run_setup_command()
        return
    settings = Settings.load()
    queue = NotificationQueue(settings.queue_file_path, settings)
    if args.command == "daemon":
        run_daemon(
            settings,
            queue,
            interval_seconds=getattr(args, "interval_seconds", None),
        )
    elif args.command == "release":
        run_release(settings, queue, focus_mode=getattr(args, "focus_mode", None))
    elif args.command == "focus":
        run_focus(settings, queue, args.state)
    elif args.command == "replay":
        run_replay(queue)
    elif args.command == "watch":
        run_watch(
            settings,
            queue,
            interval_seconds=getattr(args, "interval_seconds", None),
        )
    elif args.command == "reset-stats":
        run_reset_stats(queue)
    elif args.command == "start":
        from src.launcher import run_start

        run_start(settings, queue)
    elif args.command == "prime":
        run_prime(queue)


if __name__ == "__main__":
    main()
