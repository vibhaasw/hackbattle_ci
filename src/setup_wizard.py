"""Interactive onboarding: GitHub webhook + Slack tokens written into .env."""

from __future__ import annotations

import getpass
import json
import logging
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from src.config import ROOT

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
SLACK_API = "https://slack.com/api"
WEBHOOK_EVENTS = ["pull_request", "issues", "issue_comment"]
WEBHOOK_PATH = "/github/webhook"
PLACEHOLDER_SECRETS = {"", "replace-me"}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

ENV_KEYS = (
    "GITHUB_REPO",
    "GITHUB_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_WEBHOOK_URL",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
)

SLACK_API_HINTS = {
    "invalid_auth": "unauthorized — Slack rejected this token (invalid_auth)",
    "not_authed": "unauthorized — no credentials accepted (not_authed)",
    "account_inactive": "the Slack account or workspace is inactive",
    "token_revoked": "this token has been revoked",
    "token_expired": "this token has expired",
    "missing_scope": "missing required scope (app token needs connections:write)",
    "invalid_token": "unauthorized — Slack reported invalid_token",
    "org_login_required": "workspace SSO / org login is required for this token",
}

InputFn = Callable[[str], str]
PrintFn = Callable[[str], None]


@dataclass
class SetupResult:
    """What the wizard configured. Safe to print (no secrets)."""

    repo: str
    webhook_id: int | None
    webhook_url: str
    webhook_action: str
    slack_workspace: str
    env_path: Path
    start_watch: bool


class SetupError(Exception):
    """User-facing setup failure with a specific reason."""


def parse_repo(raw: str) -> tuple[str, str]:
    """Split `owner/repo`, raising SetupError if the shape is wrong."""
    value = (raw or "").strip().removeprefix("https://github.com/").strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not REPO_RE.match(value) or value.count("/") != 1:
        raise SetupError("Repo must look like owner/repo (for example vibhaasw/hackbattle_ci).")
    owner, repo = value.split("/", 1)
    return owner, repo


def normalize_webhook_url(raw: str) -> str:
    """Turn an ngrok origin (or full payload URL) into https://host/github/webhook."""
    value = (raw or "").strip()
    if not value:
        raise SetupError("Public webhook URL is required.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise SetupError("Webhook URL must be https:// (a live ngrok tunnel, not http://).")
    if not parsed.netloc:
        raise SetupError("Webhook URL is missing a host.")
    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        path = WEBHOOK_PATH
    elif path != WEBHOOK_PATH:
        if path.endswith(WEBHOOK_PATH):
            path = WEBHOOK_PATH
        else:
            path = path + WEBHOOK_PATH
    return f"https://{parsed.netloc}{path}"


def webhook_urls_match(left: str, right: str) -> bool:
    """True when two payload URLs point at the same GitHub hook target."""
    try:
        return normalize_webhook_url(left) == normalize_webhook_url(right)
    except SetupError:
        return False


def ensure_webhook_secret(existing: str) -> tuple[str, bool]:
    """Reuse a real secret; otherwise generate 32 random bytes as hex."""
    current = (existing or "").strip()
    if current and current not in PLACEHOLDER_SECRETS:
        return current, False
    return secrets.token_hex(32), True


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines without printing values."""
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


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Create or update KEY=VALUE lines in place. Leaves unrelated keys alone."""
    original = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in original:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={_format_env_value(updates[key])}")
            seen.add(key)
        else:
            out.append(line)
    missing = [key for key in updates if key not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# written by python -m src.main setup")
        for key in missing:
            out.append(f"{key}={_format_env_value(updates[key])}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def detect_ngrok_https_url(timeout: float = 0.5) -> str | None:
    """Return the public https URL from a local ngrok agent, if one is running."""
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=timeout)
        payload = resp.json()
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for tunnel in payload.get("tunnels") or []:
        if not isinstance(tunnel, dict):
            continue
        url = str(tunnel.get("public_url") or "")
        if url.startswith("https://"):
            return url.rstrip("/")
    return None


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when something is already accepting connections on host:port."""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def mask_secret(value: str) -> str:
    """Short, safe preview of a token (prefix + last 4)."""
    text = (value or "").strip()
    if len(text) <= 8:
        return "••••"
    return f"{text[:7]}…{text[-4:]}"


def find_hook_by_url(hooks: list[dict[str, Any]], webhook_url: str) -> dict[str, Any] | None:
    """Return the first repo hook whose config.url matches webhook_url."""
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        config = hook.get("config") or {}
        if isinstance(config, dict) and webhook_urls_match(str(config.get("url") or ""), webhook_url):
            return hook
    return None


def format_slack_api_error(error_code: str) -> str:
    """Map a Slack `error` field to a specific setup message."""
    code = (error_code or "unknown_error").strip()
    hint = SLACK_API_HINTS.get(code)
    if hint:
        return f"{hint} [{code}]"
    return f"Slack API error: {code}"


def slack_prefix_error(name: str, token: str, prefix: str) -> str | None:
    """Explain an empty or wrong-prefix Slack token, or None if the prefix is ok."""
    value = (token or "").strip()
    if not value:
        return f"{name} is empty."
    if "replace-me" in value or value.endswith("..."):
        return f"{name} is still a placeholder — paste the real token from Slack's app UI."
    if not value.startswith(prefix):
        got = value.split("-", 1)[0] + "-" if "-" in value else value[:8]
        return f"{name} has the wrong prefix (must start with {prefix}, this starts with {got})."
    return None


def github_http(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Call the GitHub REST API. Returns (status, parsed JSON or text)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "context-interrupter-setup",
    }
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    try:
        resp = requests.request(method, url, headers=headers, json=payload, timeout=20)
    except requests.exceptions.SSLError:
        resp = requests.request(
            method, url, headers=headers, json=payload, timeout=20, verify=False
        )
    except requests.RequestException as exc:
        raise SetupError(f"GitHub API request failed: {exc}") from exc
    return resp.status_code, _parse_json_body(resp)


def slack_api(method_name: str, token: str) -> dict[str, Any]:
    """POST a Slack Web API method with a bearer token."""
    try:
        resp = requests.post(
            f"{SLACK_API}/{method_name}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SetupError(f"Slack API request failed ({method_name}): {exc}") from exc
    body = _parse_json_body(resp)
    if not isinstance(body, dict):
        raise SetupError(f"Slack {method_name} returned a non-JSON response (HTTP {resp.status_code}).")
    return body


def validate_bot_token(token: str) -> dict[str, Any]:
    """auth.test the bot token. Raises SetupError with a specific reason."""
    prefix = slack_prefix_error("SLACK_BOT_TOKEN", token, "xoxb-")
    if prefix:
        raise SetupError(prefix)
    body = slack_api("auth.test", token)
    if not body.get("ok"):
        raise SetupError(f"SLACK_BOT_TOKEN failed auth.test: {format_slack_api_error(str(body.get('error') or ''))}")
    return body


def validate_app_token(token: str) -> dict[str, Any]:
    """Open a Socket Mode connection URL to prove the app token works."""
    prefix = slack_prefix_error("SLACK_APP_TOKEN", token, "xapp-")
    if prefix:
        raise SetupError(prefix)
    body = slack_api("apps.connections.open", token)
    if not body.get("ok"):
        raise SetupError(
            f"SLACK_APP_TOKEN failed Socket Mode check (apps.connections.open): "
            f"{format_slack_api_error(str(body.get('error') or ''))}"
        )
    url = str(body.get("url") or "")
    if url and not url.startswith("wss://"):
        raise SetupError("SLACK_APP_TOKEN opened Socket Mode but Slack did not return a wss:// URL.")
    return body


def get_repo(owner: str, repo: str, token: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo} so we fail before creating a hook."""
    status, body = github_http("GET", f"/repos/{owner}/{repo}", token)
    if status == 200 and isinstance(body, dict):
        return body
    raise SetupError(_github_status_error(status, body, owner, repo, action="read the repository"))


def list_hooks(owner: str, repo: str, token: str) -> list[dict[str, Any]]:
    """GET /repos/{owner}/{repo}/hooks."""
    status, body = github_http("GET", f"/repos/{owner}/{repo}/hooks?per_page=100", token)
    if status == 200 and isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    raise SetupError(_github_status_error(status, body, owner, repo, action="list webhooks"))


def create_hook(
    owner: str,
    repo: str,
    token: str,
    *,
    webhook_url: str,
    secret: str,
) -> dict[str, Any]:
    """POST /repos/{owner}/{repo}/hooks for PR/issue/comment events."""
    status, body = github_http(
        "POST",
        f"/repos/{owner}/{repo}/hooks",
        token,
        _hook_payload(webhook_url, secret),
    )
    if status in {200, 201} and isinstance(body, dict) and body.get("id") is not None:
        return body
    raise SetupError(_github_status_error(status, body, owner, repo, action="create the webhook"))


def update_hook(
    owner: str,
    repo: str,
    token: str,
    hook_id: int,
    *,
    webhook_url: str,
    secret: str,
) -> dict[str, Any]:
    """PATCH an existing hook to the current URL, secret, and events."""
    status, body = github_http(
        "PATCH",
        f"/repos/{owner}/{repo}/hooks/{hook_id}",
        token,
        _hook_payload(webhook_url, secret),
    )
    if status == 200 and isinstance(body, dict):
        return body
    raise SetupError(
        _github_status_error(status, body, owner, repo, action=f"update webhook {hook_id}")
    )


def run_setup(
    *,
    env_path: Path | None = None,
    input_fn: InputFn = input,
    getpass_fn: InputFn = getpass.getpass,
    print_fn: PrintFn | None = None,
    get_repo_fn: Callable[[str, str, str], dict[str, Any]] = get_repo,
    list_hooks_fn: Callable[[str, str, str], list[dict[str, Any]]] = list_hooks,
    create_hook_fn: Callable[..., dict[str, Any]] = create_hook,
    update_hook_fn: Callable[..., dict[str, Any]] = update_hook,
    validate_bot_fn: Callable[[str], dict[str, Any]] = validate_bot_token,
    validate_app_fn: Callable[[str], dict[str, Any]] = validate_app_token,
    detect_ngrok_fn: Callable[[], str | None] = detect_ngrok_https_url,
    port_in_use_fn: Callable[[int], bool] = port_in_use,
) -> SetupResult:
    """Run the interactive wizard and persist values into `.env`."""
    path = env_path or (ROOT / ".env")
    say = print_fn or print
    existing = load_env_file(path)

    say("")
    say("Context Interrupter setup")
    say("Configure a GitHub repo webhook and Slack Socket Mode tokens.")
    say("")

    say("— GitHub —")
    owner, repo = _prompt_repo(existing, input_fn, say)
    token_label = (
        "GitHub Personal Access Token (classic: admin:repo_hooks; "
        "fine-grained: Webhooks Read and write)"
    )
    token = _prompt_secret(token_label, existing.get("GITHUB_TOKEN", ""), getpass_fn, say)
    hooks: list[dict[str, Any]] = []
    while True:
        try:
            say(f"Checking access to {owner}/{repo}…")
            get_repo_fn(owner, repo, token)
            say(f"Looking up webhooks on {owner}/{repo}…")
            hooks = list_hooks_fn(owner, repo, token)
            break
        except SetupError as exc:
            say(str(exc))
            say("Enter a different PAT — the previous token cannot manage webhooks on this repo.")
            token = _prompt_secret(token_label, "", getpass_fn, say)

    public_url = _prompt_webhook_url(existing, input_fn, say, detect_ngrok_fn)
    webhook_url = normalize_webhook_url(public_url)
    secret, generated = ensure_webhook_secret(existing.get("GITHUB_WEBHOOK_SECRET", ""))
    if generated:
        say("Generated GITHUB_WEBHOOK_SECRET (openssl-style 32-byte hex).")
    else:
        say("Reusing GITHUB_WEBHOOK_SECRET already in .env.")
    found = find_hook_by_url(hooks, webhook_url)
    if found is not None:
        hook_id = int(found["id"])
        say(f"A webhook already exists at {webhook_url} (id {hook_id}).")
        if _yes("Update the existing webhook instead of creating a new one?", True, input_fn):
            updated = update_hook_fn(
                owner, repo, token, hook_id, webhook_url=webhook_url, secret=secret
            )
            hook_id = int(updated.get("id") or hook_id)
            action = "updated"
            say(f"Updated webhook id {hook_id}.")
        else:
            action = "reused"
            say(f"Left existing webhook id {hook_id} unchanged.")
    else:
        created = create_hook_fn(owner, repo, token, webhook_url=webhook_url, secret=secret)
        if created.get("id") is None:
            raise SetupError("GitHub accepted the webhook but did not return an id.")
        hook_id = int(created["id"])
        action = "created"
        say(f"Created webhook id {hook_id}.")

    say("")
    say("— Slack —")
    say(
        "SLACK_BOT_TOKEN and SLACK_APP_TOKEN still have to be created in Slack's UI "
        "(api.slack.com/apps → OAuth & Permissions + Socket Mode). "
        "This wizard cannot generate them."
    )
    bot_token = _prompt_secret(
        "SLACK_BOT_TOKEN (xoxb-…)", existing.get("SLACK_BOT_TOKEN", ""), getpass_fn, say
    )
    bot_info = _retry_validate(
        "SLACK_BOT_TOKEN", bot_token, validate_bot_fn, getpass_fn, say, "SLACK_BOT_TOKEN (xoxb-…)"
    )
    bot_token = bot_info["token"]
    workspace = str(bot_info["body"].get("team") or bot_info["body"].get("team_id") or "")
    say(f"Bot token ok — workspace: {workspace or '(name not returned)'}.")

    app_token = _prompt_secret(
        "SLACK_APP_TOKEN (xapp-…)", existing.get("SLACK_APP_TOKEN", ""), getpass_fn, say
    )
    app_info = _retry_validate(
        "SLACK_APP_TOKEN", app_token, validate_app_fn, getpass_fn, say, "SLACK_APP_TOKEN (xapp-…)"
    )
    app_token = app_info["token"]
    say("App token ok — Socket Mode connection check passed.")

    updates = {
        "GITHUB_REPO": f"{owner}/{repo}",
        "GITHUB_TOKEN": token,
        "GITHUB_WEBHOOK_SECRET": secret,
        "GITHUB_WEBHOOK_URL": webhook_url,
        "SLACK_BOT_TOKEN": bot_token,
        "SLACK_APP_TOKEN": app_token,
    }
    upsert_env(path, updates)
    say(f"Wrote {path} (other keys left as they were).")
    say("")
    say("Configured:")
    say(f"  Repo:        {owner}/{repo}")
    say(f"  Webhook ID:  {hook_id} ({action})")
    say(f"  Payload URL: {webhook_url}")
    say(f"  Slack:       {workspace or '(unknown workspace)'}")
    say("")

    start = _yes("Start watching now?", True, input_fn)
    if start and port_in_use_fn(9001):
        say("Port 9001 is already in use — not starting a second watch process.")
        start = False
    elif start:
        say("Starting watch…")

    return SetupResult(
        repo=f"{owner}/{repo}",
        webhook_id=hook_id,
        webhook_url=webhook_url,
        webhook_action=action,
        slack_workspace=workspace,
        env_path=path,
        start_watch=start,
    )


def _hook_payload(webhook_url: str, secret: str) -> dict[str, Any]:
    return {
        "name": "web",
        "active": True,
        "events": list(WEBHOOK_EVENTS),
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }


def _parse_json_body(resp: requests.Response) -> Any:
    if not resp.text:
        return {}
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _github_status_error(status: int, body: Any, owner: str, repo: str, *, action: str) -> str:
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")
    elif isinstance(body, str):
        message = body.strip()[:200]
    extra = f" GitHub said: {message}" if message else ""
    target = f"{owner}/{repo}"
    if status == 401:
        return f"GitHub token was rejected (unauthorized) while trying to {action} on {target}.{extra}"
    if status == 403:
        return (
            f"GitHub denied access (403) while trying to {action} on {target}. "
            "The PAT needs admin:repo_hooks (classic) or Webhooks: Read and write "
            f"(fine-grained).{extra}"
        )
    if status == 404:
        return (
            f"GitHub returned 404 while trying to {action} on {target}. "
            f"The repo may not exist, or this token cannot see it.{extra}"
        )
    if status == 422:
        return f"GitHub rejected the webhook payload (422) for {target}.{extra}"
    return f"GitHub HTTP {status} while trying to {action} on {target}.{extra}"


def _format_env_value(value: str) -> str:
    if any(ch in value for ch in (' ', '#', '"', "'")):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _ask(prompt: str, default: str, input_fn: InputFn) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input_fn(f"{prompt}{suffix}: ")
    return (raw or "").strip() or default


def _yes(prompt: str, default: bool, input_fn: InputFn) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input_fn(f"{prompt} ({hint}) ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _prompt_repo(existing: dict[str, str], input_fn: InputFn, say: PrintFn) -> tuple[str, str]:
    default = (existing.get("GITHUB_REPO") or existing.get("GITHUB_TEST_REPO") or "").strip()
    if default == "owner/repo":
        default = ""
    while True:
        raw = _ask("GitHub repo (owner/repo)", default, input_fn)
        try:
            return parse_repo(raw)
        except SetupError as exc:
            say(str(exc))


def _prompt_webhook_url(
    existing: dict[str, str],
    input_fn: InputFn,
    say: PrintFn,
    detect_ngrok_fn: Callable[[], str | None],
) -> str:
    say(
        "Public webhook URL — this must be a live ngrok HTTPS tunnel already running, "
        "pointing at port 9001 (example: https://abc123.ngrok-free.dev). "
        "/github/webhook is appended automatically if you omit it."
    )
    detected = None
    try:
        detected = detect_ngrok_fn()
    except Exception:
        detected = None
    default = (existing.get("GITHUB_WEBHOOK_URL") or "").strip()
    if detected:
        say(f"Detected a local ngrok tunnel: {detected}")
        if not default:
            default = detected
    while True:
        raw = _ask("Public webhook URL (ngrok HTTPS → port 9001)", default, input_fn)
        try:
            return normalize_webhook_url(raw)
        except SetupError as exc:
            say(str(exc))


def _prompt_secret(label: str, existing: str, input_fn: InputFn, say: PrintFn) -> str:
    current = (existing or "").strip()
    keepable = bool(current) and current not in PLACEHOLDER_SECRETS and "replace-me" not in current
    if keepable:
        say(f"{label} is already set ({mask_secret(current)}). Press Enter to keep it.")
    while True:
        raw = input_fn(f"{label}: ").strip()
        if raw:
            return raw
        if keepable:
            return current
        say(f"{label} is required.")


def _retry_validate(
    name: str,
    token: str,
    validate_fn: Callable[[str], dict[str, Any]],
    input_fn: InputFn,
    say: PrintFn,
    relabel: str,
) -> dict[str, Any]:
    current = token
    while True:
        try:
            body = validate_fn(current)
            return {"token": current, "body": body}
        except SetupError as exc:
            say(f"{name} was rejected: {exc}")
            current = input_fn(f"{relabel}: ").strip()
            if not current:
                say(f"{name} is required.")
