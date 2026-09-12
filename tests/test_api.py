"""HTTP API over queue + release logic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import attach_runtime_routes, to_ui_item
from src.config import Settings
from src.github_listener import create_app
from src.notification import Notification
from src.queue import NotificationQueue
from src.setup_wizard import SetupResult


def _settings(tmp: Path) -> Settings:
    base = Settings.load()
    return Settings(
        github_webhook_host=base.github_webhook_host,
        github_webhook_port=base.github_webhook_port,
        github_webhook_secret="test-secret",
        github_token=base.github_token,
        ollama_url=base.ollama_url,
        ollama_model=base.ollama_model,
        ollama_timeout_seconds=base.ollama_timeout_seconds,
        queue_file_path=tmp / "queue.json",
        check_interval_seconds=base.check_interval_seconds,
        git_commit_trigger=base.git_commit_trigger,
        build_success_trigger=base.build_success_trigger,
        manual_trigger_enabled=base.manual_trigger_enabled,
        max_queue_size=base.max_queue_size,
        auto_clear_after_days=base.auto_clear_after_days,
        calendar_gate_enabled=False,
        avg_context_switch_cost_minutes=base.avg_context_switch_cost_minutes,
    )


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"api_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.dir)
        self.queue = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.queue.add(
            Notification(
                source="github",
                type="issue",
                author="Ada",
                title="Broken deploy",
                url="https://example.com/1",
                raw_id=99,
                summary="[Issue] Ada: Broken deploy",
                timestamp=1.0,
            )
        )
        app = create_app(self.queue, webhook_secret="test-secret")
        attach_runtime_routes(app, self.queue, self.settings)
        self.client = app.test_client()

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def test_queue_and_dismiss_reuse_queue(self) -> None:
        resp = self.client.get("/api/queue")
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()["notifications"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Broken deploy")
        self.assertEqual(items[0]["projectId"], "github")
        nid = items[0]["id"]
        gone = self.client.post(f"/api/notifications/{nid}/dismiss")
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(self.client.get("/api/queue").get_json()["notifications"], [])

    def test_release_uses_release_logic(self) -> None:
        resp = self.client.post("/api/release", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["released"])
        self.assertGreaterEqual(body["stats"]["releases_today"], 1)

    def test_setup_maps_errors(self) -> None:
        with patch(
            "src.api.configure_from_values",
            side_effect=__import__("src.setup_wizard", fromlist=["SetupError"]).SetupError(
                "GitHub token was rejected (unauthorized)"
            ),
        ):
            resp = self.client.post("/api/setup", json={"github_token": "nope"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unauthorized", resp.get_json()["error"])

    def test_setup_success(self) -> None:
        result = SetupResult(
            repo="acme/demo",
            webhook_id=7,
            webhook_url="https://abc.ngrok-free.dev/github/webhook",
            webhook_action="created",
            slack_workspace="HackBattle",
            env_path=self.dir / ".env",
            start_watch=False,
        )
        with patch("src.api.configure_from_values", return_value=result):
            resp = self.client.post(
                "/api/setup",
                json={"github_repo": "acme/demo", "github_token": "tok"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["webhook_id"], 7)

    def test_prime_clears_pending_and_stats(self) -> None:
        resp = self.client.post("/api/prime")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertGreaterEqual(body["dismissed"], 1)
        self.assertEqual(body["stats"]["interruptions_caught_today"], 0)
        self.assertEqual(self.client.get("/api/queue").get_json()["notifications"], [])

    def test_workflows_patch_retags_pending(self) -> None:
        listed = self.client.get("/api/workflows")
        self.assertEqual(listed.status_code, 200)
        patched = self.client.patch("/api/workflows/github", json={"urgency_profile": "quiet"})
        self.assertEqual(patched.status_code, 200)
        updated = next(row for row in patched.get_json()["workflows"] if row["id"] == "github")
        self.assertEqual(updated["urgency_profile"], "quiet")
        item = self.client.get("/api/queue").get_json()["notifications"][0]
        self.assertEqual(item["projectId"], "github")
        self.assertEqual(item["urgency"], "low")

    def test_to_ui_item_ms_timestamp(self) -> None:
        mapped = to_ui_item(
            {
                "id": "github-issue-1",
                "author": "Ada",
                "type": "issue",
                "title": "Hi",
                "source": "github",
                "timestamp": 10,
            }
        )
        self.assertEqual(mapped["timestamp"], 10000)
        self.assertEqual(mapped["subtitle"], "Ada · issue")


if __name__ == "__main__":
    unittest.main()
