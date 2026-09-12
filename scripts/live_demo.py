#!/usr/bin/env python3
"""Fire one real GitHub issue so the dashboard can show a live capture.

Keeps Slack as a manual @mention — that is the judge-visible second source.
Does not print tokens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "[LIVE DEMO]"


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        out[key.strip()] = value.strip().strip("'").strip('"')
    return out


def gh_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


def gh_json(args: list[str]) -> dict:
    raw = subprocess.check_output(["gh", "api", *args], text=True, env=gh_env())
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SystemExit(f"unexpected gh response: {parsed!r}")
    return parsed


def queue_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["id"])
        for item in data.get("notifications") or []
        if isinstance(item, dict) and item.get("id")
    }


def main() -> None:
    os.chdir(ROOT)
    env = load_env(ROOT / ".env")
    repo = (env.get("GITHUB_REPO") or env.get("GITHUB_TEST_REPO") or "").strip()
    if not repo or "/" not in repo:
        raise SystemExit("GITHUB_REPO is not set. Save owner/repo in the dashboard Connect panel first.")
    qpath = ROOT / (env.get("QUEUE_FILE_PATH") or "queue.json")
    before = queue_ids(qpath)
    title = f"{MARKER} judge capture {int(time.time())}"
    print(f"Opening a real issue on {repo}: {title}")
    created = gh_json(
        [
            "--method",
            "POST",
            f"repos/{repo}/issues",
            "-f",
            f"title={title}",
            "-f",
            f"body={MARKER} opened to prove webhook → queue → dashboard. Safe to close.",
        ]
    )
    number = created.get("number")
    issue_id = created.get("id")
    print(f"  opened {repo}#{number} (github-issue-{issue_id})")
    print("  waiting up to 45s for the dashboard queue…")
    captured = None
    deadline = time.time() + 45
    expected = f"github-issue-{issue_id}"
    while time.time() < deadline:
        data = json.loads(qpath.read_text(encoding="utf-8")) if qpath.exists() else {}
        for item in data.get("notifications") or []:
            if item.get("id") == expected or (
                item.get("id") not in before and str(item.get("id", "")).startswith("github-")
            ):
                if not item.get("read") and not item.get("deferred"):
                    captured = item
                    break
        if captured:
            break
        time.sleep(1)
    try:
        gh_json(["--method", "PATCH", f"repos/{repo}/issues/{number}", "-f", "state=closed"])
        print(f"  closed {repo}#{number}")
    except subprocess.CalledProcessError as exc:
        print(f"  could not close {repo}#{number}: {exc}")

    if captured:
        print(f"CAPTURED {captured.get('id')} — it should appear in the github pane within 5s")
        print(f"  title: {captured.get('title')}")
    else:
        print("NOT captured. Check that `python -m src.main start` is running and ngrok")
        print("points at :9001, then open Settings → Webhooks on the repo.")
        sys.exit(2)

    channel = env.get("SLACK_TEST_CHANNEL_ID") or "your Slack channel"
    print()
    print("Slack (second live source, do this in front of judges):")
    print(f"  In channel {channel}, @mention the bot with a short real sentence.")
    print("  The slack pane should add a row without a refresh.")
    print()
    print("Dashboard: http://127.0.0.1:9001/  (hard-refresh once if the page looks stale)")


if __name__ == "__main__":
    main()
