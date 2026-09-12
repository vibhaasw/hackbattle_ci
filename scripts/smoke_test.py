#!/usr/bin/env python3
"""Repeatable capture → summarize → queue smoke test. Dev tool only — not product code."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
MARKER = "[SMOKE TEST]"
SLACK_API = "https://slack.com/api"
GITHUB_API = "https://api.github.com"

console = Console()


def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip("'").strip('"')
    return out


def merged_env() -> dict[str, str]:
    """`.env` wins over leftover shell exports so QUEUE_FILE_PATH stays the daemon's."""
    env = dict(os.environ)
    env.update(load_dotenv(ROOT / ".env"))
    return env


def queue_path(env: dict[str, str]) -> Path:
    raw = (env.get("QUEUE_FILE_PATH") or "queue.json").strip()
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def webhook_port(env: dict[str, str]) -> int:
    try:
        return int((env.get("GITHUB_WEBHOOK_PORT") or "9001").strip())
    except ValueError:
        return 9001


def read_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"notifications": []}
    return json.loads(path.read_text(encoding="utf-8"))


def notification_index(path: Path) -> dict[str, dict[str, Any]]:
    data = read_queue(path)
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("notifications") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


def ids_matching(index: dict[str, dict[str, Any]], prefix: str) -> set[str]:
    return {nid for nid in index if nid.startswith(prefix)}


def http_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any] | str]:
    req_headers = {"User-Agent": "context-interrupter-smoke"}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    if headers:
        req_headers.update(headers)
    kwargs: dict[str, Any] = {"headers": req_headers, "timeout": timeout}
    if payload is not None:
        kwargs["json"] = payload
    try:
        resp = requests.request(method, url, **kwargs)
    except requests.exceptions.SSLError:
        console.print(
            "  [yellow]TLS verify failed (local proxy/self-signed chain); retrying once[/yellow]"
        )
        resp = requests.request(method, url, verify=False, **kwargs)
    except requests.exceptions.RequestException as exc:
        return 0, str(exc)
    try:
        parsed: dict[str, Any] | str = resp.json() if resp.text else {}
    except ValueError:
        parsed = resp.text
    return int(resp.status_code), parsed


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def poll_new(
    path: Path,
    prefix: str,
    before: set[str],
    timeout: float,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            index = notification_index(path)
        except (OSError, json.JSONDecodeError):
            index = {}
        new_ids = ids_matching(index, prefix) - before
        for nid in sorted(new_ids):
            item = index[nid]
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            raw = item.get("raw_data") if isinstance(item.get("raw_data"), dict) else {}
            if MARKER in title or MARKER in summary or raw.get("smoke_test"):
                return item
        remaining = max(0, int(deadline - time.time()))
        console.print(f"  [dim]polling {path.name} for {prefix}* … {remaining}s[/dim]", end="\r")
        time.sleep(1)
    console.print()
    return None


def _dismiss_existing_smoke(path: Path) -> None:
    """Leave prior smoke rows in the file but out of the pending TUI."""
    try:
        index = notification_index(path)
    except (OSError, json.JSONDecodeError):
        return
    for nid, item in index.items():
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or "")
        raw = item.get("raw_data") if isinstance(item.get("raw_data"), dict) else {}
        if MARKER in title or MARKER in summary or raw.get("smoke_test"):
            tag_and_dismiss(path, nid)


def tag_and_dismiss(path: Path, notification_id: str) -> None:
    """Mark the captured row so it is obvious and leaves the pending TUI."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    items = data.get("notifications")
    if not isinstance(items, list):
        return
    changed = False
    for item in items:
        if not isinstance(item, dict) or str(item.get("id")) != notification_id:
            continue
        summary = str(item.get("summary") or item.get("title") or "")
        if not summary.startswith(MARKER):
            item["summary"] = f"{MARKER} {summary}".strip()
        raw = item.get("raw_data")
        if not isinstance(raw, dict):
            raw = {}
        raw["smoke_test"] = True
        item["raw_data"] = raw
        item["read"] = True
        changed = True
    if not changed:
        return
    tmp = path.with_suffix(path.suffix + ".smoke.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def slack_api(method: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    verb = "GET" if payload is None else "POST"
    status, body = http_json(f"{SLACK_API}/{method}", method=verb, token=token, payload=payload)
    if not isinstance(body, dict):
        raise RuntimeError(f"slack {method}: HTTP {status} {body}")
    if status == 0:
        raise RuntimeError(f"slack {method}: {body}")
    if not body.get("ok"):
        raise RuntimeError(f"slack {method}: {body.get('error') or body}")
    return body


def run_slack(env: dict[str, str], qpath: Path) -> tuple[bool, str]:
    token = (env.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (env.get("SLACK_TEST_CHANNEL_ID") or "").strip()
    if not token.startswith("xoxb-") or "replace-me" in token:
        return False, "SLACK_BOT_TOKEN missing or placeholder"
    if not channel or channel.startswith("C012345"):
        return False, "SLACK_TEST_CHANNEL_ID is not set to a real channel"

    try:
        auth = slack_api("auth.test", token)
    except RuntimeError as exc:
        return False, str(exc)
    bot_id = str(auth.get("user_id") or "")
    if not bot_id:
        return False, "auth.test did not return user_id"
    console.print(f"  Slack auth.test ok (bot user {bot_id})")

    before = ids_matching(notification_index(qpath), "slack-mention-")
    text = f"<@{bot_id}> {MARKER} automated smoke test — safe to ignore"
    ts = ""
    try:
        posted = slack_api(
            "chat.postMessage",
            token,
            {"channel": channel, "text": text},
        )
        ts = str(posted.get("ts") or "")
        console.print(f"  posted mention in test channel (ts={ts or '?'})")
    except RuntimeError as exc:
        return False, str(exc)

    try:
        captured = poll_new(qpath, "slack-mention-", before, timeout=30)
        if captured is None:
            return False, "no new slack-mention-* in queue.json within 30s"
        tag_and_dismiss(qpath, str(captured["id"]))
        return True, (
            f"{captured['id']}, urgency: {captured.get('urgency') or 'normal'}, "
            f"summary: {captured.get('summary') or captured.get('title')}"
        )
    finally:
        if ts:
            try:
                slack_api("chat.delete", token, {"channel": channel, "ts": ts})
                console.print("  deleted smoke Slack message")
            except RuntimeError as exc:
                console.print(f"  [yellow]chat.delete failed: {exc}[/yellow]")


def count_smoke_slack_messages(env: dict[str, str]) -> int:
    """How many of our smoke messages are still visible in the test channel."""
    token = (env.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (env.get("SLACK_TEST_CHANNEL_ID") or "").strip()
    try:
        hist = slack_api(
            "conversations.history",
            token,
            {"channel": channel, "limit": 20},
        )
    except RuntimeError:
        return -1
    count = 0
    for msg in hist.get("messages") or []:
        if MARKER in str(msg.get("text") or ""):
            count += 1
    return count


def run_github_synthetic(env: dict[str, str], qpath: Path) -> tuple[bool, str]:
    console.print(
        Panel(
            "SYNTHETIC — tests pipeline logic only, not live webhook delivery.",
            border_style="yellow",
            title="GitHub mode",
        )
    )
    port = webhook_port(env)
    if not port_open(port):
        return False, f"nothing listening on 127.0.0.1:{port} (start: python -m src.main watch)"

    issue_id = int(time.time())
    payload = {
        "action": "opened",
        "issue": {
            "id": issue_id,
            "number": issue_id % 100000,
            "title": f"{MARKER} automated pipeline check",
            "html_url": f"https://github.com/smoke-test/ci/issues/{issue_id}",
            "user": {"login": "smoke-bot"},
            "body": f"{MARKER} synthetic webhook — safe to ignore",
        },
    }
    before = ids_matching(notification_index(qpath), "github-issue-")
    body = json.dumps(payload).encode("utf-8")
    secret = (env.get("GITHUB_WEBHOOK_SECRET") or "").strip()
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "issues"}
    if secret and secret != "replace-me":
        import hashlib
        import hmac

        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={digest}"

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/github/webhook",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = int(resp.status)
            resp_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, f"webhook HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"webhook request failed: {exc}"

    if status != 200:
        return False, f"webhook HTTP {status}: {resp_body}"
    console.print(f"  POST /github/webhook → HTTP {status} {resp_body}")

    captured = poll_new(qpath, "github-issue-", before, timeout=15)
    expected = f"github-issue-{issue_id}"
    if captured is None:
        # accept the id we just posted even if marker match raced
        index = notification_index(qpath)
        captured = index.get(expected)
    if captured is None:
        return False, f"no {expected} in queue.json after 200"
    tag_and_dismiss(qpath, str(captured["id"]))
    return True, (
        f"{captured['id']}, urgency: {captured.get('urgency') or 'normal'}, "
        f"summary: {captured.get('summary') or captured.get('title')}"
    )


def run_github_live(env: dict[str, str], qpath: Path) -> tuple[bool, str]:
    console.print(
        Panel(
            "LIVE — tests the full real webhook delivery path including ngrok.",
            border_style="cyan",
            title="GitHub mode",
        )
    )
    token = (env.get("GITHUB_TEST_TOKEN") or "").strip()
    repo = (env.get("GITHUB_TEST_REPO") or "").strip()
    if not token or "replace-me" in token:
        return False, "GITHUB_TEST_TOKEN is not set"
    if not repo or "/" not in repo or repo == "owner/repo":
        return False, "GITHUB_TEST_REPO must be owner/repo"

    before = ids_matching(notification_index(qpath), "github-issue-")
    title = f"{MARKER} automated pipeline check {int(time.time())}"
    status, created = http_json(
        f"{GITHUB_API}/repos/{repo}/issues",
        method="POST",
        token=token,
        payload={
            "title": title,
            "body": f"{MARKER} live smoke test — this issue will be closed immediately.",
        },
        headers={"Accept": "application/vnd.github+json"},
    )
    if status not in {200, 201} or not isinstance(created, dict) or "number" not in created:
        return False, f"create issue failed HTTP {status}: {created}"
    number = created["number"]
    html_url = created.get("html_url")
    console.print(f"  opened {repo}#{number} ({html_url})")

    try:
        captured = poll_new(qpath, "github-issue-", before, timeout=45)
        if captured is None:
            return False, f"no new github-issue-* in queue.json within 45s after opening {repo}#{number}"
        tag_and_dismiss(qpath, str(captured["id"]))
        return True, (
            f"{captured['id']}, urgency: {captured.get('urgency') or 'normal'}, "
            f"summary: {captured.get('summary') or captured.get('title')}"
        )
    finally:
        close_status, closed = http_json(
            f"{GITHUB_API}/repos/{repo}/issues/{number}",
            method="PATCH",
            token=token,
            payload={"state": "closed"},
            headers={"Accept": "application/vnd.github+json"},
        )
        if close_status in {200, 201} and isinstance(closed, dict):
            console.print(f"  closed {repo}#{number}")
        else:
            console.print(f"  [yellow]failed to close {repo}#{number}: HTTP {close_status} {closed}[/yellow]")


def render_summary(
    slack: tuple[bool, str],
    github: tuple[bool, str],
    *,
    github_mode: str,
) -> None:
    table = Table(title="Smoke test", show_lines=False)
    table.add_column("Check")
    table.add_column("Status", width=8)
    table.add_column("Evidence")
    rows = [
        ("Slack live capture", slack),
        (
            f"GitHub {github_mode} capture",
            github,
        ),
    ]
    for label, (ok, detail) in rows:
        status = "PASS" if ok else "FAIL"
        style = "green" if ok else "red"
        table.add_row(label, f"[{style}]{status}[/{style}]", detail)
    console.print()
    console.print(table)
    if github_mode == "synthetic":
        console.print()
        console.print(
            "[yellow]This did not test live webhook delivery — run with "
            "--github-mode live occasionally, especially before the demo, "
            "to catch ngrok/network issues synthetic mode can't see.[/yellow]"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repeatable Slack + GitHub capture smoke test")
    parser.add_argument(
        "--github-mode",
        choices=("synthetic", "live"),
        default="synthetic",
        help="synthetic (default): local POST to :9001. live: real GitHub issue via API.",
    )
    return parser


def main() -> None:
    os.chdir(ROOT)
    args = build_parser().parse_args()
    env = merged_env()
    qpath = queue_path(env)
    console.print(Panel.fit("[bold]Context Interrupter — smoke test[/bold]", border_style="cyan"))
    console.print(f"queue: {qpath}")
    console.print(f"github mode: {args.github_mode}")
    _dismiss_existing_smoke(qpath)

    console.print("\n[bold]Slack[/bold] (live API — real app_mention)")
    try:
        slack = run_slack(env, qpath)
    except Exception as exc:  # noqa: BLE001 — never crash the report
        slack = (False, f"uncaught error: {exc}")
    console.print(f"  {'PASS' if slack[0] else 'FAIL'} — {slack[1]}")

    console.print(f"\n[bold]GitHub[/bold] ({args.github_mode})")
    try:
        if args.github_mode == "live":
            github = run_github_live(env, qpath)
        else:
            github = run_github_synthetic(env, qpath)
    except Exception as exc:  # noqa: BLE001
        github = (False, f"uncaught error: {exc}")
    console.print(f"  {'PASS' if github[0] else 'FAIL'} — {github[1]}")

    render_summary(slack, github, github_mode=args.github_mode)
    sys.exit(0 if slack[0] and github[0] else 1)


if __name__ == "__main__":
    main()
