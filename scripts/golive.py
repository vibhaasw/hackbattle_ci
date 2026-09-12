#!/usr/bin/env python3
"""Interactive go-live checklist. Observes and triggers; does not change product logic."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
DAEMON_LOG = LOG_DIR / "daemon.log"
OLLAMA_LOG = ROOT / "logs" / "ollama.log"
QUEUE_DEFAULT = ROOT / "queue.json"
PLACEHOLDERS = {"", "replace-me", "xoxb-replace-me", "xapp-replace-me", "xoxb-...", "xapp-..."}

Status = str  # pending | pass | fail | wait
ICON = {"pending": "⬜", "pass": "✅", "fail": "❌", "wait": "⏳"}

console = Console()


def load_dotenv_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file. Does not print values."""
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
    """Repo `.env` first, then process env so test overrides win."""
    merged = load_dotenv_file(ROOT / ".env")
    merged.update(os.environ)
    return merged


def python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def ollama_url(env: dict[str, str] | None = None) -> str:
    src = env if env is not None else merged_env()
    return (src.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")


def webhook_port(env: dict[str, str] | None = None) -> int:
    src = env if env is not None else merged_env()
    raw = (src.get("GITHUB_WEBHOOK_PORT") or "9001").strip()
    try:
        return int(raw)
    except ValueError:
        return 9001


def queue_path(env: dict[str, str] | None = None) -> Path:
    src = env if env is not None else merged_env()
    raw = (src.get("QUEUE_FILE_PATH") or "queue.json").strip()
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def read_queue(path: Path | None = None) -> dict[str, Any]:
    target = path or queue_path()
    return json.loads(target.read_text(encoding="utf-8"))


def notification_ids(data: dict[str, Any], source: str | None = None) -> set[str]:
    items = data.get("notifications") or []
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if source and str(item.get("source") or "") != source:
            continue
        nid = item.get("id")
        if nid:
            ids.add(str(nid))
    return ids


def http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return int(resp.status), body


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.create_connection((host, port), timeout=timeout):
        return True


def run_captured(argv: list[str], *, timeout: float | None = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def print_proc(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        console.print(result.stdout.rstrip())
    if result.returncode != 0:
        console.print(f"[red]exit {result.returncode}[/red]")
        if result.stderr:
            console.print(f"[red]{result.stderr.rstrip()}[/red]")


# ---------------------------------------------------------------------------
# Automated checks (pure; used by the menu and by --auto)
# ---------------------------------------------------------------------------


def check_ollama(env: dict[str, str] | None = None) -> tuple[bool, str]:
    url = ollama_url(env)
    try:
        status, _ = http_get(f"{url}/api/tags", timeout=3.0)
        return status < 400, f"GET {url}/api/tags → HTTP {status}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"GET {url}/api/tags failed: {exc}"


def check_env_tokens(env: dict[str, str] | None = None) -> tuple[bool, str]:
    src = env if env is not None else merged_env()
    bot = (src.get("SLACK_BOT_TOKEN") or "").strip()
    app = (src.get("SLACK_APP_TOKEN") or "").strip()
    problems: list[str] = []
    if not bot.startswith("xoxb-") or bot in PLACEHOLDERS or "replace-me" in bot:
        problems.append("SLACK_BOT_TOKEN is missing, placeholder, or not xoxb-")
    if not app.startswith("xapp-") or app in PLACEHOLDERS or "replace-me" in app:
        problems.append("SLACK_APP_TOKEN is missing, placeholder, or not xapp-")
    if problems:
        return False, "; ".join(problems)
    return True, "SLACK_BOT_TOKEN is xoxb- and SLACK_APP_TOKEN is xapp- (not placeholders)"


def check_daemon_port(env: dict[str, str] | None = None) -> tuple[bool, str]:
    port = webhook_port(env)
    try:
        port_open("127.0.0.1", port)
        return True, f"127.0.0.1:{port} accepted a TCP connection"
    except OSError as exc:
        return False, f"127.0.0.1:{port} refused: {exc}"


def check_queue_schema(path: Path | None = None) -> tuple[bool, str]:
    target = path or queue_path()
    if not target.exists():
        return False, f"{target} does not exist"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f"{target} is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return False, "queue root must be an object"
    missing = [k for k in ("notifications", "settings", "stats") if k not in data]
    if missing:
        return False, f"missing keys: {', '.join(missing)}"
    if not isinstance(data["notifications"], list):
        return False, "notifications must be a list"
    if not isinstance(data["settings"], dict):
        return False, "settings must be an object"
    if not isinstance(data["stats"], dict):
        return False, "stats must be an object"
    return True, f"{target.name} has notifications + settings + stats"


def check_git_clean() -> tuple[bool, str]:
    tracked_env = run_captured(["git", "ls-files", "--error-unmatch", ".env"], timeout=10)
    if tracked_env.returncode == 0:
        return False, ".env is tracked by git — remove it from the index"
    porcelain = run_captured(["git", "status", "--porcelain", "-uall"], timeout=10)
    if porcelain.returncode != 0:
        return False, porcelain.stderr.strip() or "git status failed"
    problems: list[str] = []
    for line in porcelain.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path == ".env" or path.endswith("/.env"):
            problems.append(".env is visible to git (untracked or dirty)")
        if path.startswith("src/") and line[:2].strip():
            problems.append(f"unexpected dirty core file: {path}")
    if problems:
        return False, "; ".join(problems)
    return True, "no tracked .env; src/ has no unexpected uncommitted files"


def check_focus_analytics(path: Path | None = None) -> tuple[bool, str]:
    target = path or queue_path()
    if not target.exists():
        return False, f"{target} does not exist"
    try:
        data = read_queue(target)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"cannot read queue: {exc}"
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    try:
        caught = int(stats.get("interruptions_caught_today") or 0)
        minutes = float(stats.get("focus_minutes_protected_today") or 0)
        cost = float(
            settings.get("avg_context_switch_cost_minutes")
            or merged_env().get("AVG_CONTEXT_SWITCH_COST_MINUTES")
            or 17.5
        )
    except (TypeError, ValueError) as exc:
        return False, f"stats are not numeric: {exc}"
    expected = caught * cost
    if abs(minutes - expected) > 0.01:
        return False, f"focus minutes {minutes} != {caught} × {cost} (= {expected})"
    return True, f"{minutes:g} min = {caught} interruptions × {cost:g}"


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@dataclass
class Item:
    key: str
    section: str
    title: str
    kind: str  # auto | launch | manual
    status: Status = "pending"
    detail: str = ""
    run: Callable[["GoLive"], None] | None = field(default=None, repr=False)


class GoLive:
    """Session-scoped checklist runner. Status lives only in this process."""

    def __init__(self) -> None:
        self.daemon_proc: subprocess.Popen[str] | None = None
        self.ollama_proc: subprocess.Popen[str] | None = None
        self.items: list[Item] = self._build_items()
        self.by_key = {item.key: item for item in self.items}

    def _build_items(self) -> list[Item]:
        return [
            Item("cold.ollama", "Cold Start", "Ollama reachable", "auto", run=self._do_ollama),
            Item("cold.ollama_serve", "Cold Start", "Start ollama serve if needed", "launch", run=self._do_ollama_serve),
            Item("cold.tokens", "Cold Start", ".env has real Slack tokens", "auto", run=self._do_tokens),
            Item("cold.port", "Cold Start", "Daemon port responding", "auto", run=self._do_port),
            Item("cold.daemon", "Cold Start", "Start daemon (background)", "launch", run=self._do_start_daemon),
            Item("cap.slack", "Dual-Source Capture", "Slack DM / @mention captured", "manual", run=self._do_slack_capture),
            Item("cap.github", "Dual-Source Capture", "GitHub issue / PR captured", "manual", run=self._do_github_capture),
            Item("trig.release", "Triggers", "Manual release", "launch", run=self._do_release),
            Item("trig.focus_on", "Triggers", "Focus on holds the queue", "launch", run=self._do_focus_on),
            Item("trig.focus_off", "Triggers", "Focus off clears the gate", "launch", run=self._do_focus_off),
            Item("trig.commit", "Triggers", "Commit trigger releases the queue", "manual", run=self._do_commit_trigger),
            Item("trig.timer", "Triggers", "Timer trigger releases the queue", "manual", run=self._do_timer_trigger),
            Item("tui.keys", "TUI Interaction", "Keyboard: defer / dismiss / open / quit", "launch", run=self._do_tui),
            Item("ana.focus", "Focus Analytics", "Focus minutes match interruptions × 17.5", "auto", run=self._do_analytics),
            Item("persist.schema", "Restart Persistence", "queue.json schema is valid", "auto", run=self._do_schema),
            Item("persist.restart", "Restart Persistence", "Queue survives a daemon restart", "manual", run=self._do_persist),
            Item("sign.git", "Final Sign-Off", "Git tree has no unexpected dirty src/ or .env", "auto", run=self._do_git),
        ]

    def set_status(self, key: str, status: Status, detail: str) -> None:
        item = self.by_key[key]
        item.status = status
        item.detail = detail
        color = {"pass": "green", "fail": "red", "wait": "yellow", "pending": "white"}[status]
        console.print(f"  {ICON[status]}  [{color}]{item.title}[/{color}] — {detail}")

    # -- automated --------------------------------------------------------

    def _do_ollama(self) -> None:
        ok, detail = check_ollama()
        self.set_status("cold.ollama", "pass" if ok else "fail", detail)

    def _do_tokens(self) -> None:
        ok, detail = check_env_tokens()
        self.set_status("cold.tokens", "pass" if ok else "fail", detail)

    def _do_port(self) -> None:
        ok, detail = check_daemon_port()
        self.set_status("cold.port", "pass" if ok else "fail", detail)

    def _do_schema(self) -> None:
        ok, detail = check_queue_schema()
        self.set_status("persist.schema", "pass" if ok else "fail", detail)

    def _do_analytics(self) -> None:
        ok, detail = check_focus_analytics()
        self.set_status("ana.focus", "pass" if ok else "fail", detail)

    def _do_git(self) -> None:
        ok, detail = check_git_clean()
        self.set_status("sign.git", "pass" if ok else "fail", detail)

    # -- launch -----------------------------------------------------------

    def _do_ollama_serve(self) -> None:
        ok, detail = check_ollama()
        if ok:
            self.set_status("cold.ollama_serve", "pass", f"already running ({detail})")
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = OLLAMA_LOG.open("a", encoding="utf-8")
        try:
            self.ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            log.close()
            self.set_status("cold.ollama_serve", "fail", f"could not spawn ollama serve: {exc}")
            return
        console.print(f"  started pid {self.ollama_proc.pid}; log → {OLLAMA_LOG}")
        time.sleep(1.5)
        ok, detail = check_ollama()
        self.set_status("cold.ollama_serve", "pass" if ok else "fail", detail)
        self.by_key["cold.ollama"].status = "pass" if ok else "fail"
        self.by_key["cold.ollama"].detail = detail

    def _do_start_daemon(self) -> None:
        ok, detail = check_daemon_port()
        if ok:
            self.set_status("cold.daemon", "pass", f"already listening ({detail})")
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = DAEMON_LOG.open("a", encoding="utf-8")
        try:
            self.daemon_proc = subprocess.Popen(
                [python_bin(), "-m", "src.main", "watch"],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            log.close()
            self.set_status("cold.daemon", "fail", f"could not spawn daemon: {exc}")
            return
        console.print(f"  started pid {self.daemon_proc.pid}; log → {DAEMON_LOG}")
        deadline = time.time() + 8
        last = "still starting"
        while time.time() < deadline:
            if self.daemon_proc.poll() is not None:
                tail = _tail(DAEMON_LOG, 20)
                self.set_status(
                    "cold.daemon",
                    "fail",
                    f"daemon exited {self.daemon_proc.returncode}\n{tail}",
                )
                return
            ok, last = check_daemon_port()
            if ok:
                self.set_status("cold.daemon", "pass", last)
                self.by_key["cold.port"].status = "pass"
                self.by_key["cold.port"].detail = last
                return
            time.sleep(0.4)
        tail = _tail(DAEMON_LOG, 20)
        self.set_status("cold.daemon", "fail", f"port never opened: {last}\n{tail}")

    def _cli(self, *args: str, inherit: bool = False, timeout: float | None = 60) -> subprocess.CompletedProcess[str]:
        argv = [python_bin(), "-m", "src.main", *args]
        console.print(f"  [dim]$ {' '.join(argv)}[/dim]")
        if inherit:
            return subprocess.run(argv, cwd=ROOT, check=False)
        result = run_captured(argv, timeout=timeout)
        print_proc(result)
        return result

    def _do_release(self) -> None:
        result = self._cli("release", inherit=True)
        if result.returncode == 0:
            self.set_status("trig.release", "pass", "release exited 0")
        else:
            self.set_status("trig.release", "fail", f"release exited {result.returncode}")

    def _do_focus_on(self) -> None:
        on = self._cli("focus", "on")
        if on.returncode != 0:
            self.set_status("trig.focus_on", "fail", f"focus on exited {on.returncode}")
            return
        rel = self._cli("release")
        text = (rel.stdout or "") + (rel.stderr or "")
        if rel.returncode == 0 and "held" in text.lower():
            self.set_status("trig.focus_on", "pass", "focus on + release printed the held message")
        else:
            self.set_status(
                "trig.focus_on",
                "fail",
                f"expected 'held' in release output (exit {rel.returncode})",
            )

    def _do_focus_off(self) -> None:
        off = self._cli("focus", "off")
        if off.returncode != 0:
            self.set_status("trig.focus_off", "fail", f"focus off exited {off.returncode}")
            return
        self.set_status("trig.focus_off", "pass", "focus off exited 0")

    def _do_tui(self) -> None:
        console.print(
            "[yellow]TUI will take over this terminal. "
            "Try [bold]d 1[/bold], [bold]x 1[/bold], [bold]o 1[/bold], then [bold]q[/bold].[/yellow]"
        )
        if not self._confirm("Open release TUI now?"):
            self.set_status("tui.keys", "wait", "skipped — run this item when you can use the TUI")
            return
        before = _flag_snapshot()
        result = self._cli("release", inherit=True)
        after = _flag_snapshot()
        if result.returncode != 0:
            self.set_status("tui.keys", "fail", f"release exited {result.returncode}")
            return
        if before is not None and after is not None and before != after:
            self.set_status("tui.keys", "pass", "queue flags changed after TUI (defer/dismiss landed)")
        else:
            self.set_status("tui.keys", "pass", "release TUI exited 0 (no defer/dismiss change detected)")

    # -- manual then verify ----------------------------------------------

    def _do_slack_capture(self) -> None:
        self._capture_from_source(
            "cap.slack",
            source="slack",
            instructions=(
                "In Slack, DM the bot or @mention it in a channel it has joined.\n"
                "The running daemon should log Queued slack-... and append queue.json."
            ),
        )

    def _do_github_capture(self) -> None:
        self._capture_from_source(
            "cap.github",
            source="github",
            instructions=(
                "Open or comment on a GitHub issue/PR against the webhook, "
                "or POST a payload to http://127.0.0.1:9001/github/webhook.\n"
                "A new github-* id should appear in queue.json."
            ),
        )

    def _capture_from_source(self, key: str, *, source: str, instructions: str) -> None:
        path = queue_path()
        before: set[str] = set()
        if path.exists():
            try:
                before = notification_ids(read_queue(path), source)
            except (OSError, json.JSONDecodeError) as exc:
                console.print(f"[red]Could not snapshot {path}: {exc}[/red]")
        console.print(Panel(instructions, title=f"{source} capture", border_style="yellow"))
        console.print(f"  snapshot: {len(before)} existing {source} id(s)")
        if not self._confirm("Press Enter after you have sent the event (or skip)"):
            self.set_status(key, "wait", "waiting for an outside event")
            return
        found = self._poll_new_ids(path, source, before, timeout=90)
        if found:
            self.set_status(key, "pass", f"new {source} id(s): {', '.join(sorted(found))}")
        else:
            self.set_status(key, "fail", f"no new {source} notification in queue.json within 90s")

    def _poll_new_ids(self, path: Path, source: str, before: set[str], timeout: float) -> set[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if path.exists():
                try:
                    current = notification_ids(read_queue(path), source)
                except (OSError, json.JSONDecodeError):
                    current = set()
                new = current - before
                if new:
                    return new
            remaining = max(0, int(deadline - time.time()))
            console.print(f"  [dim]waiting for a new {source} row… {remaining}s[/dim]", end="\r")
            time.sleep(2)
        console.print()
        return set()

    def _do_commit_trigger(self) -> None:
        last = _last_release_at()
        console.print(
            Panel(
                "In another terminal:\n"
                "  .venv/bin/python -m src.main watch\n"
                "Then make a real commit in this repo (or a throwaway clone pointed at the same queue).\n"
                "This item passes when queue.json stats.last_release_at advances.",
                title="Commit trigger",
                border_style="yellow",
            )
        )
        console.print(f"  last_release_at before: {last}")
        if not self._confirm("Press Enter after the commit has released (or skip)"):
            self.set_status("trig.commit", "wait", "waiting for a commit-triggered release")
            return
        now = _last_release_at()
        if now is not None and (last is None or now > last):
            self.set_status("trig.commit", "pass", f"last_release_at advanced to {now}")
        else:
            self.set_status("trig.commit", "fail", f"last_release_at unchanged ({now})")

    def _do_timer_trigger(self) -> None:
        last = _last_release_at()
        console.print(
            Panel(
                "In another terminal, with pending items and focus off:\n"
                "  .venv/bin/python -m src.main watch --interval-seconds 10\n"
                "Wait for 'Timer elapsed' / Auto release. Then come back here.",
                title="Timer trigger",
                border_style="yellow",
            )
        )
        console.print(f"  last_release_at before: {last}")
        if not self._confirm("Press Enter after the timer has released (or skip)"):
            self.set_status("trig.timer", "wait", "waiting for a timer-triggered release")
            return
        now = _last_release_at()
        if now is not None and (last is None or now > last):
            self.set_status("trig.timer", "pass", f"last_release_at advanced to {now}")
        else:
            self.set_status("trig.timer", "fail", f"last_release_at unchanged ({now})")

    def _do_persist(self) -> None:
        path = queue_path()
        ok, detail = check_queue_schema(path)
        if not ok:
            self.set_status("persist.restart", "fail", detail)
            return
        before_ids = sorted(notification_ids(read_queue(path)))
        fingerprint = json.dumps(before_ids)
        console.print(
            Panel(
                f"Recorded {len(before_ids)} notification id(s) from {path.name}.\n"
                "Restart the daemon now (stop the old process, start "
                "`python -m src.main watch` again).\n"
                "Do not wipe queue.json.",
                title="Restart persistence",
                border_style="yellow",
            )
        )
        if not self._confirm("Press Enter after the daemon is back"):
            self.set_status("persist.restart", "wait", "waiting for a daemon restart")
            return
        try:
            after_ids = sorted(notification_ids(read_queue(path)))
        except (OSError, json.JSONDecodeError) as exc:
            self.set_status("persist.restart", "fail", f"queue unreadable after restart: {exc}")
            return
        if json.dumps(after_ids) == fingerprint:
            self.set_status("persist.restart", "pass", f"{len(after_ids)} id(s) still present after restart")
        else:
            self.set_status(
                "persist.restart",
                "fail",
                f"ids changed\nbefore={before_ids}\nafter={after_ids}",
            )

    def _confirm(self, message: str) -> bool:
        answer = Prompt.ask(f"  {message}", default="enter").strip().lower()
        return answer not in {"s", "skip", "n", "no"}

    # -- views ------------------------------------------------------------

    def render_menu(self, target: Console | None = None) -> None:
        out = target or console
        out.print()
        out.print(Panel.fit("[bold]Context Interrupter — Go Live[/bold]", border_style="cyan"))
        current = ""
        index = 1
        for item in self.items:
            if item.section != current:
                current = item.section
                out.print(f"\n[bold cyan]{current}[/bold cyan]")
            icon = ICON[item.status]
            kind = {"auto": "auto", "launch": "run", "manual": "manual"}[item.kind]
            out.print(f"  [bold]{index:2}[/bold]  {icon}  {item.title}  [dim]({kind})[/dim]")
            index += 1
        out.print()
        out.print("[dim]number = run item   a = full sequence   s = summary   l = daemon log   q = quit[/dim]")

    def render_summary(self, target: Console | None = None) -> None:
        out = target or console
        table = Table(title="Go-live summary / Definition of Done", show_lines=False)
        table.add_column("DoD")
        table.add_column("Status", width=8)
        table.add_column("Evidence")
        mapping = [
            ("GitHub and Slack captured live", ["cap.github", "cap.slack"]),
            ("LLM / Ollama path (or fallback still queues)", ["cold.ollama"]),
            ("Queue persists across restarts", ["persist.schema", "persist.restart"]),
            ("Non-timer trigger live (commit or focus hold)", ["trig.commit", "trig.focus_on"]),
            ("TUI displays and is navigable", ["tui.keys", "trig.release"]),
            ("Focus-analytics stat accurate", ["ana.focus"]),
            (".env tokens present; daemon listening", ["cold.tokens", "cold.port", "cold.daemon"]),
            ("Working tree has no leaked .env / dirty src/", ["sign.git"]),
        ]
        for label, keys in mapping:
            statuses = [self.by_key[k].status for k in keys]
            if any(s == "fail" for s in statuses):
                status = "FAIL"
            elif all(s == "pass" for s in statuses):
                status = "PASS"
            elif any(s == "wait" for s in statuses):
                status = "WAIT"
            else:
                status = "PENDING"
            evidence = "; ".join(
                f"{ICON[self.by_key[k].status]} {self.by_key[k].title}" for k in keys
            )
            style = {"PASS": "green", "FAIL": "red", "WAIT": "yellow", "PENDING": "dim"}[status]
            table.add_row(label, f"[{style}]{status}[/{style}]", evidence)
        out.print(table)
        out.print()
        counts = {name: 0 for name in ICON}
        for item in self.items:
            counts[item.status] += 1
        out.print(
            f"Items  {ICON['pass']} {counts['pass']} pass   "
            f"{ICON['fail']} {counts['fail']} fail   "
            f"{ICON['wait']} {counts['wait']} waiting   "
            f"{ICON['pending']} {counts['pending']} not checked"
        )

    def run_item(self, item: Item) -> None:
        console.print(f"\n[bold]→ {item.section} / {item.title}[/bold]")
        if item.run is None:
            self.set_status(item.key, "wait", "no handler")
            return
        try:
            item.run()
        except Exception as exc:  # noqa: BLE001 — tool must not crash the menu
            console.print_exception(show_locals=False)
            self.set_status(item.key, "fail", f"uncaught error: {exc}")

    def run_full_sequence(self) -> None:
        console.print("\n[bold]Full sequence — autos run now; launches and manuals pause when needed.[/bold]")
        for item in self.items:
            if item.kind == "auto":
                self.run_item(item)
            elif item.kind == "launch":
                if self._confirm(f"Run launch step: {item.title}?"):
                    self.run_item(item)
                else:
                    item.status = "wait"
                    item.detail = "skipped in full sequence"
            else:
                self.run_item(item)
        self.render_summary()

    def tail_daemon_log(self) -> None:
        if not DAEMON_LOG.exists():
            console.print(f"[yellow]No log yet at {DAEMON_LOG}[/yellow]")
            return
        console.print(Panel(_tail(DAEMON_LOG, 40) or "(empty)", title=str(DAEMON_LOG), border_style="blue"))

    def loop(self) -> None:
        while True:
            self.render_menu()
            choice = Prompt.ask(">").strip().lower()
            if choice in {"q", "quit", "exit"}:
                return
            if choice in {"s", "summary"}:
                self.render_summary()
                continue
            if choice in {"l", "log"}:
                self.tail_daemon_log()
                continue
            if choice in {"a", "all"}:
                self.run_full_sequence()
                continue
            if choice.isdigit():
                index = int(choice)
                if 1 <= index <= len(self.items):
                    self.run_item(self.items[index - 1])
                else:
                    console.print("[red]No item with that number[/red]")
                continue
            console.print("[red]Unknown command[/red]")


def _tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read log: {exc})"
    chunk = text.splitlines()[-lines:]
    return "\n".join(chunk)


def _last_release_at() -> float | None:
    path = queue_path()
    if not path.exists():
        return None
    try:
        stats = read_queue(path).get("stats") or {}
        raw = stats.get("last_release_at")
        return None if raw is None else float(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _flag_snapshot() -> list[tuple[str, bool, bool]] | None:
    path = queue_path()
    if not path.exists():
        return None
    try:
        data = read_queue(path)
    except (OSError, json.JSONDecodeError):
        return None
    rows: list[tuple[str, bool, bool]] = []
    for item in data.get("notifications") or []:
        if isinstance(item, dict) and item.get("id"):
            rows.append((str(item["id"]), bool(item.get("read")), bool(item.get("deferred"))))
    return sorted(rows)


def run_auto(session: GoLive) -> int:
    """Run every automated check. Returns 1 if any failed."""
    console.print(Panel.fit("[bold]Automated go-live checks[/bold]", border_style="cyan"))
    autos = [item for item in session.items if item.kind == "auto"]
    for item in autos:
        session.run_item(item)
    console.print()
    session.render_summary()
    return 1 if any(item.status == "fail" for item in autos) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive go-live checklist")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run automated checks only and exit",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    os.chdir(ROOT)
    session = GoLive()
    if args.auto:
        sys.exit(run_auto(session))
    try:
        session.loop()
    except KeyboardInterrupt:
        console.print("\n[dim]bye[/dim]")


if __name__ == "__main__":
    main()
