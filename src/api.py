"""Small HTTP API over the existing queue, release logic, and setup helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from src.analytics import reset_stats, stats_snapshot
from src.config import ROOT, Settings
from src.queue import NotificationQueue
from src.release_logic import ReleaseLogic
from src.setup_wizard import (
    SetupError,
    configure_from_values,
    detect_ngrok_https_url,
    load_env_file,
    mask_secret,
    port_in_use,
)

logger = logging.getLogger(__name__)

WEB_DIST = ROOT / "web" / "dist"


def to_ui_item(item: dict[str, Any]) -> dict[str, Any]:
    """Map a queue snapshot row to the dashboard notification shape."""
    ts = float(item.get("timestamp") or 0)
    if ts and ts < 1e12:
        ts = ts * 1000
    source = str(item.get("source") or "github")
    return {
        "id": item.get("id"),
        "urgency": item.get("urgency") or "normal",
        "title": item.get("title") or "",
        "subtitle": f"{item.get('author') or 'unknown'} · {item.get('type') or ''}",
        "summary": item.get("summary") or item.get("title") or "",
        "source": source,
        "timestamp": ts,
        "projectId": source,
        "workflowId": item.get("workflow_id") or source,
        "url": item.get("url") or "",
    }


def public_status(settings: Settings, queue: NotificationQueue) -> dict[str, Any]:
    """Connection state with secrets masked — safe for the dashboard."""
    env = load_env_file(ROOT / ".env")
    ngrok = detect_ngrok_https_url()
    stats = stats_snapshot(queue)
    return {
        "repo": env.get("GITHUB_REPO") or env.get("GITHUB_TEST_REPO") or "",
        "webhook_url": env.get("GITHUB_WEBHOOK_URL") or "",
        "ngrok_url": ngrok or "",
        "github_token_set": bool(env.get("GITHUB_TOKEN") and "replace-me" not in env.get("GITHUB_TOKEN", "")),
        "github_token_preview": mask_secret(env.get("GITHUB_TOKEN", "")),
        "slack_bot_set": bool(env.get("SLACK_BOT_TOKEN", "").startswith("xoxb-")),
        "slack_app_set": bool(env.get("SLACK_APP_TOKEN", "").startswith("xapp-")),
        "webhook_port": settings.github_webhook_port,
        "webhook_listening": port_in_use(settings.github_webhook_port),
        "stats": stats,
    }


def register_api(app: Flask, queue: NotificationQueue, settings: Settings) -> None:
    """Attach /api/* routes that call through queue + ReleaseLogic."""

    @app.after_request
    def _cors(resp):  # type: ignore[no-untyped-def]
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,OPTIONS")
        return resp

    @app.get("/api/health", endpoint="api_health")
    def api_health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/api/status")
    def api_status() -> tuple[dict[str, Any], int]:
        queue.load()
        return public_status(settings, queue), 200

    @app.get("/api/queue")
    def api_queue() -> tuple[dict[str, Any], int]:
        queue.load()
        from src.workflows import hydrate_pending

        workflows = hydrate_pending(queue)
        items = [to_ui_item(row) for row in queue.get_queue_snapshot()]
        return {
            "notifications": items,
            "stats": stats_snapshot(queue),
            "workflows": workflows,
        }, 200

    @app.get("/api/stats")
    def api_stats() -> tuple[dict[str, Any], int]:
        queue.load()
        return stats_snapshot(queue), 200

    @app.post("/api/release")
    def api_release() -> tuple[dict[str, Any], int]:
        queue.load()
        body = request.get_json(silent=True) or {}
        override = None
        if body.get("focus_mode") in {"on", "off"}:
            override = body["focus_mode"] == "on"
        logic = ReleaseLogic(queue, settings, focus_mode_override=override)
        if logic.is_held():
            return {
                "released": False,
                "held": True,
                "warning": logic.gate_warning,
                "notifications": [],
                "stats": stats_snapshot(queue),
            }, 200
        items = logic.manual_release()
        return {
            "released": bool(items),
            "held": False,
            "warning": logic.gate_warning,
            "notifications": [to_ui_item(row) for row in items],
            "stats": stats_snapshot(queue),
        }, 200

    @app.post("/api/notifications/<notif_id>/dismiss")
    def dismiss(notif_id: str) -> tuple[dict[str, Any], int]:
        queue.load()
        ok = queue.dismiss(notif_id)
        return {"ok": ok, "id": notif_id}, 200 if ok else 404

    @app.post("/api/notifications/<notif_id>/defer")
    def defer(notif_id: str) -> tuple[dict[str, Any], int]:
        queue.load()
        ok = queue.defer(notif_id)
        return {"ok": ok, "id": notif_id}, 200 if ok else 404

    @app.post("/api/prime")
    def api_prime() -> tuple[dict[str, Any], int]:
        queue.load()
        cleared = queue.dismiss_all_pending()
        previous = reset_stats(queue)
        return {
            "ok": True,
            "dismissed": cleared,
            "previous_stats": previous,
            "stats": stats_snapshot(queue),
            "notifications": [],
        }, 200

    @app.get("/api/workflows")
    def api_workflows() -> tuple[dict[str, Any], int]:
        queue.load()
        from src.urgency import profiles
        from src.workflows import ensure_workflows

        return {"workflows": ensure_workflows(queue), "profiles": profiles()}, 200

    @app.put("/api/workflows")
    def api_put_workflows() -> tuple[dict[str, Any], int]:
        queue.load()
        body = request.get_json(silent=True) or {}
        rows = body.get("workflows") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return {"ok": False, "error": "body must include a workflows array"}, 400
        from src.urgency import profiles
        from src.workflows import save_workflows

        saved = save_workflows(queue, rows)
        return {"ok": True, "workflows": saved, "profiles": profiles()}, 200

    @app.patch("/api/workflows/<workflow_id>")
    def api_patch_workflow(workflow_id: str) -> tuple[dict[str, Any], int]:
        queue.load()
        body = request.get_json(silent=True) or {}
        from src.urgency import profiles
        from src.workflows import ensure_workflows, save_workflows

        rows = ensure_workflows(queue)
        found = False
        for item in rows:
            if item.get("id") != workflow_id:
                continue
            found = True
            for key in ("name", "urgency_profile", "color"):
                if key in body and body[key] is not None:
                    item[key] = str(body[key])
            for key in ("github_repos", "slack_channels", "keywords"):
                if key in body and isinstance(body[key], list):
                    item[key] = [str(v) for v in body[key]]
            if "catch_all" in body:
                item["catch_all"] = bool(body["catch_all"])
        if not found:
            return {"ok": False, "error": f"unknown workflow {workflow_id}"}, 404
        saved = save_workflows(queue, rows)
        return {"ok": True, "workflows": saved, "profiles": profiles()}, 200

    @app.post("/api/setup")
    def api_setup() -> tuple[dict[str, Any], int]:
        body = request.get_json(silent=True) or {}
        try:
            result = configure_from_values(
                github_repo=str(body.get("github_repo") or ""),
                github_token=str(body.get("github_token") or ""),
                slack_bot_token=str(body.get("slack_bot_token") or ""),
                slack_app_token=str(body.get("slack_app_token") or ""),
                public_url=body.get("public_url") or detect_ngrok_https_url(),
                env_path=ROOT / ".env",
            )
        except SetupError as exc:
            logger.info("Setup rejected: %s", exc)
            return {"ok": False, "error": str(exc)}, 400
        queue.load()
        if result.repo:
            from src.workflows import upsert_repo_workflow

            upsert_repo_workflow(queue, result.repo)
        return {
            "ok": True,
            "repo": result.repo,
            "webhook_id": result.webhook_id,
            "webhook_url": result.webhook_url,
            "webhook_action": result.webhook_action,
            "slack_workspace": result.slack_workspace,
            "status": public_status(settings, queue),
        }, 200


def register_frontend(app: Flask, dist: Path | None = None) -> None:
    """Serve the built dashboard from web/dist without shadowing /api or /github."""
    root = dist or WEB_DIST

    @app.get("/", endpoint="dashboard_index")
    def dashboard_index():  # type: ignore[no-untyped-def]
        if (root / "index.html").exists():
            return send_from_directory(root, "index.html")
        return (
            jsonify(
                {
                    "error": "dashboard not built",
                    "hint": "cd web && npm install && npm run build",
                }
            ),
            503,
        )

    @app.get("/<path:asset>", endpoint="dashboard_asset")
    def dashboard_asset(asset: str):  # type: ignore[no-untyped-def]
        if asset.startswith("api/") or asset.startswith("github/"):
            return jsonify({"error": "not found"}), 404
        target = root / asset
        if target.is_file():
            return send_from_directory(root, asset)
        if (root / "index.html").exists():
            return send_from_directory(root, "index.html")
        return jsonify({"error": "not found"}), 404


def attach_runtime_routes(app: Flask, queue: NotificationQueue, settings: Settings) -> None:
    """API + dashboard on the same Flask app as the GitHub webhook."""
    register_api(app, queue, settings)
    register_frontend(app)
