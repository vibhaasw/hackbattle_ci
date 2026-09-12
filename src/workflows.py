"""Route notifications into user-defined workflows (repos, channels, keywords)."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

from src.notification import Notification
from src.queue import NotificationQueue
from src.urgency import PROFILE_IDS, classify

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_COLORS = ("#38BDF8", "#A78BFA", "#34D399", "#F59E0B", "#F472B6")
_GITHUB_REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")


def slug(name: str) -> str:
    """Stable id from a display name or owner/repo."""
    value = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return value or "inbox"


def default_workflows() -> list[dict[str, Any]]:
    """GitHub + Slack panes are the product. Inbox is only a fallback bucket."""
    channel = (os.getenv("SLACK_TEST_CHANNEL_ID") or "").strip()
    return [
        _workflow(
            "github",
            "github",
            sources=["github"],
            color=_COLORS[0],
            urgency_profile="shipping",
        ),
        _workflow(
            "slack",
            "slack",
            sources=["slack"],
            slack_channels=[channel] if channel else [],
            color=_COLORS[1],
            urgency_profile="oncall",
        ),
        _workflow("inbox", "inbox", catch_all=True, color=_COLORS[2]),
    ]


def ensure_workflows(queue: NotificationQueue) -> list[dict[str, Any]]:
    """Load workflows from queue settings, always keeping github + slack panes."""
    raw = queue.queue_settings.get("workflows")
    if isinstance(raw, list) and raw:
        rows = [_normalize_workflow(item, index) for index, item in enumerate(raw) if isinstance(item, dict)]
        return _ensure_source_panes(queue, rows)
    seeded = default_workflows()
    queue.queue_settings["workflows"] = seeded
    queue.save()
    return seeded


def _ensure_source_panes(queue: NotificationQueue, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Older queues only had inbox — put GitHub and Slack back without wiping extras."""
    ids = {str(item.get("id")) for item in rows}
    added: list[dict[str, Any]] = []
    if "github" not in ids:
        added.append(
            _workflow("github", "github", sources=["github"], color=_COLORS[0], urgency_profile="shipping")
        )
    if "slack" not in ids:
        added.append(
            _workflow("slack", "slack", sources=["slack"], color=_COLORS[1], urgency_profile="oncall")
        )
    if not added:
        return rows
    merged = added + rows
    queue.queue_settings["workflows"] = merged
    queue.save()
    return merged


def save_workflows(queue: NotificationQueue, workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist workflow rules and retag pending items with the new profiles."""
    cleaned = [_normalize_workflow(item, index) for index, item in enumerate(workflows) if isinstance(item, dict)]
    if not any(item.get("catch_all") for item in cleaned):
        cleaned.append(_workflow("inbox", "inbox", catch_all=True, color=_COLORS[1]))
    queue.queue_settings["workflows"] = cleaned
    retag_pending(queue, cleaned)
    queue.save()
    return cleaned


def upsert_repo_workflow(queue: NotificationQueue, repo: str) -> list[dict[str, Any]]:
    """Ensure the connected GitHub repo has its own pane, not just inbox."""
    owner_repo = (repo or "").strip()
    rules = ensure_workflows(queue)
    if not owner_repo or "/" not in owner_repo or owner_repo == "owner/repo":
        return rules
    wanted = owner_repo.lower()
    for item in rules:
        repos = [str(r).lower() for r in item.get("github_repos") or []]
        if wanted in repos or item.get("id") == slug(owner_repo):
            current = [str(r) for r in item.get("github_repos") or []]
            if owner_repo not in current and wanted not in {r.lower() for r in current}:
                current.append(owner_repo)
                item["github_repos"] = current
            return save_workflows(queue, rules)
    named = _workflow(
        slug(owner_repo),
        owner_repo.split("/")[-1],
        github_repos=[owner_repo],
        color=_COLORS[len(rules) % len(_COLORS)],
    )
    others = [item for item in rules if not item.get("catch_all")]
    catch = [item for item in rules if item.get("catch_all")]
    return save_workflows(queue, others + [named] + catch)


def assign(notification: Notification, workflows: list[dict[str, Any]]) -> str:
    """Pick the first matching workflow, else the catch-all."""
    repo = github_repo_of(notification)
    channel = slack_channel_of(notification)
    raw = notification.raw_data if isinstance(notification.raw_data, dict) else {}
    blob = " ".join(
        [notification.title, str(raw.get("text") or ""), str(raw.get("body") or "")]
    ).lower()
    catch_all = "inbox"
    source_hit = ""
    for item in workflows:
        if item.get("catch_all"):
            catch_all = str(item.get("id") or "inbox")
            continue
        repos = {str(r).lower() for r in item.get("github_repos") or []}
        channels = {str(c) for c in item.get("slack_channels") or []}
        keywords = [str(k).lower() for k in item.get("keywords") or [] if str(k).strip()]
        if repo and repo.lower() in repos:
            return str(item["id"])
        if channel and channel in channels:
            return str(item["id"])
        if keywords and any(word in blob for word in keywords):
            return str(item["id"])
        sources = {str(s).lower() for s in item.get("sources") or []}
        if notification.source in sources:
            source_hit = str(item["id"])
    return source_hit or catch_all or notification.source


def profile_for(workflow_id: str, workflows: list[dict[str, Any]]) -> str:
    """Urgency profile attached to a workflow, default shipping."""
    for item in workflows:
        if item.get("id") == workflow_id:
            profile = str(item.get("urgency_profile") or "shipping")
            return profile if profile in PROFILE_IDS else "shipping"
    return "shipping"


def apply(notification: Notification, workflows: list[dict[str, Any]]) -> None:
    """Set workflow_id and urgency from rules. Does not touch summary."""
    notification.workflow_id = assign(notification, workflows)
    if any(item.get("id") == notification.source for item in workflows):
        profile = profile_for(notification.source, workflows)
    else:
        profile = profile_for(notification.workflow_id, workflows)
    notification.urgency = classify(notification, profile)


def hydrate_pending(queue: NotificationQueue) -> list[dict[str, Any]]:
    """Ensure every pending item has a workflow_id and a code-assigned urgency."""
    rules = ensure_workflows(queue)
    dirty = False
    for notif in queue.get_pending():
        if notif.workflow_id and notif.urgency in {"urgent", "normal", "low"}:
            continue
        apply(notif, rules)
        dirty = True
    if dirty:
        queue.save()
    return rules


def retag_pending(queue: NotificationQueue, workflows: list[dict[str, Any]] | None = None) -> int:
    """Re-run routing + urgency on unread items after a profile change."""
    rules = workflows if workflows is not None else ensure_workflows(queue)
    changed = 0
    for notif in queue.notifications.values():
        if notif.read or notif.deferred:
            continue
        before = (notif.workflow_id, notif.urgency)
        apply(notif, rules)
        if before != (notif.workflow_id, notif.urgency):
            changed += 1
    return changed


def github_repo_of(notification: Notification) -> str:
    """owner/repo from the URL or payload, if present."""
    url = notification.url or ""
    match = _GITHUB_REPO_RE.search(url)
    if match:
        return match.group(1)
    raw = notification.raw_data if isinstance(notification.raw_data, dict) else {}
    repo = raw.get("repository") or raw.get("repo")
    if isinstance(repo, dict):
        full = repo.get("full_name") or ""
        if isinstance(full, str) and "/" in full:
            return full
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc.endswith("github.com") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def slack_channel_of(notification: Notification) -> str:
    """Slack channel id from the stored event, if any."""
    raw = notification.raw_data if isinstance(notification.raw_data, dict) else {}
    return str(raw.get("channel") or "")


def _workflow(
    workflow_id: str,
    name: str,
    *,
    github_repos: list[str] | None = None,
    slack_channels: list[str] | None = None,
    keywords: list[str] | None = None,
    sources: list[str] | None = None,
    urgency_profile: str = "shipping",
    color: str = "#38BDF8",
    catch_all: bool = False,
) -> dict[str, Any]:
    source_list = [str(s) for s in (sources or []) if str(s).strip()]
    if workflow_id in {"github", "slack"} and not source_list:
        source_list = [workflow_id]
    return {
        "id": workflow_id,
        "name": name,
        "github_repos": list(github_repos or []),
        "slack_channels": list(slack_channels or []),
        "keywords": list(keywords or []),
        "sources": source_list,
        "urgency_profile": urgency_profile if urgency_profile in PROFILE_IDS else "shipping",
        "color": color,
        "catch_all": catch_all,
    }


def _normalize_workflow(item: dict[str, Any], index: int) -> dict[str, Any]:
    name = str(item.get("name") or item.get("id") or f"workflow-{index + 1}")
    workflow_id = str(item.get("id") or slug(name))
    return _workflow(
        workflow_id,
        name,
        github_repos=[str(r) for r in item.get("github_repos") or [] if str(r).strip()],
        slack_channels=[str(c) for c in item.get("slack_channels") or [] if str(c).strip()],
        keywords=[str(k) for k in item.get("keywords") or [] if str(k).strip()],
        sources=[str(s) for s in item.get("sources") or [] if str(s).strip()],
        urgency_profile=str(item.get("urgency_profile") or "shipping"),
        color=str(item.get("color") or _COLORS[index % len(_COLORS)]),
        catch_all=bool(item.get("catch_all")),
    )
