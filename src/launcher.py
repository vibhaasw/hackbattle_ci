"""One-command start: ngrok tunnel, webhook sync, then watch + dashboard API."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from src.config import ROOT, Settings
from src.queue import NotificationQueue
from src.setup_wizard import (
    SetupError,
    configure_from_values,
    detect_ngrok_https_url,
    load_env_file,
    port_in_use,
)

logger = logging.getLogger(__name__)


def ensure_ngrok(port: int = 9001, timeout: float = 20.0) -> str:
    """Return the current ngrok https URL, starting `ngrok http <port>` if needed."""
    existing = detect_ngrok_https_url()
    if existing:
        logger.info("Using existing ngrok tunnel %s", existing)
        return existing
    ngrok = shutil.which("ngrok")
    if not ngrok:
        raise SetupError(
            "ngrok is not on PATH. Install it and run `ngrok http 9001`, or start it first."
        )
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "ngrok.log"
    handle = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        [ngrok, "http", str(port), "--log=stdout"],
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    logger.info("Started ngrok http %s", port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = detect_ngrok_https_url()
        if url:
            logger.info("ngrok public URL %s", url)
            return url
        time.sleep(0.4)
    raise SetupError("ngrok started but did not publish an https URL in time (check logs/ngrok.log).")


def sync_webhook_from_ngrok(env_path: Path | None = None) -> dict[str, object]:
    """Point the GitHub webhook at the live ngrok URL. No-op without repo+token."""
    path = env_path or (ROOT / ".env")
    env = load_env_file(path)
    repo = (env.get("GITHUB_REPO") or env.get("GITHUB_TEST_REPO") or "").strip()
    token = (env.get("GITHUB_TOKEN") or "").strip()
    if not repo or not token or "replace-me" in token:
        logger.info("Skipping webhook sync — GITHUB_REPO / GITHUB_TOKEN not set yet")
        return {"action": "skipped", "reason": "missing_github_credentials"}
    url = detect_ngrok_https_url()
    if not url:
        logger.info("Skipping webhook sync — ngrok URL not available yet")
        return {"action": "skipped", "reason": "no_ngrok"}
    result = configure_from_values(
        github_repo=repo,
        github_token=token,
        public_url=url,
        env_path=path,
        update_existing=True,
    )
    logger.info(
        "Webhook %s id=%s url=%s",
        result.webhook_action,
        result.webhook_id,
        result.webhook_url,
    )
    return {
        "action": result.webhook_action,
        "webhook_id": result.webhook_id,
        "webhook_url": result.webhook_url,
        "repo": result.repo,
    }


def ensure_web_build() -> None:
    """Build web/dist if the dashboard bundle is missing."""
    index = ROOT / "web" / "dist" / "index.html"
    if index.exists():
        return
    npm = shutil.which("npm")
    package = ROOT / "web" / "package.json"
    if npm is None or not package.exists():
        logger.warning("Dashboard not built (web/dist missing)")
        return
    logger.info("Building dashboard (web/)")
    subprocess.run([npm, "install"], cwd=ROOT / "web", check=False)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "web", check=False)


def run_start(settings: Settings, queue: NotificationQueue) -> None:
    """User-facing entry: tunnel + webhook + listeners + dashboard."""
    from src.main import run_watch

    ensure_web_build()
    port = settings.github_webhook_port
    try:
        public = ensure_ngrok(port)
        print(f"ngrok tunnel: {public}")
        print(f"GitHub payload URL: {public}/github/webhook")
    except SetupError as exc:
        logger.warning("%s", exc)
        print(f"ngrok: {exc}")
        print("Dashboard and Slack still start. GitHub webhooks need a public HTTPS URL.")

    try:
        synced = sync_webhook_from_ngrok()
        if synced.get("action") not in {"skipped", None}:
            print(
                f"GitHub webhook {synced.get('action')} "
                f"(id {synced.get('webhook_id')}) on {synced.get('repo')}"
            )
        elif synced.get("reason") == "missing_github_credentials":
            print("Open the dashboard and paste your GitHub repo + PAT to finish webhook setup.")
    except SetupError as exc:
        logger.warning("Webhook sync failed: %s", exc)
        print(f"Webhook sync: {exc}")

    if port_in_use(port):
        print(f"http://127.0.0.1:{port}/ is already serving — not starting a second process.")
        print("Open that URL for the dashboard. Stop the other process to relaunch cleanly.")
        return

    print(f"Dashboard: http://127.0.0.1:{port}/")
    print("Enter GitHub + Slack tokens in Connect if you have not already.")
    run_watch(settings, queue)
