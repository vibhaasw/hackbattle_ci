"""GitHub listener tests using canned webhook payloads."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.github_listener import create_app, normalize_github_event
from src.queue import NotificationQueue

PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "id": 456,
        "title": "Add OAuth2 support",
        "html_url": "https://github.com/myorg/auth-service/pull/456",
        "user": {"login": "Sarah"},
    },
}

ISSUE_PAYLOAD = {
    "action": "opened",
    "issue": {
        "id": 789,
        "title": "Deploy failed in prod",
        "html_url": "https://github.com/myorg/auth-service/issues/12",
        "user": {"login": "Bob"},
    },
}

COMMENT_PAYLOAD = {
    "action": "created",
    "issue": {"id": 12, "title": "Deploy failed in prod"},
    "comment": {
        "id": 999,
        "body": "Can someone take a look?",
        "html_url": "https://github.com/myorg/auth-service/issues/12#comment-999",
        "user": {"login": "Alice"},
    },
}


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
        calendar_gate_enabled=base.calendar_gate_enabled,
        avg_context_switch_cost_minutes=base.avg_context_switch_cost_minutes,
    )


class GitHubListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"gh_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(self.dir)
        self.queue = NotificationQueue(self.settings.queue_file_path, self.settings)
        self.app = create_app(self.queue, webhook_secret="test-secret")
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def _post(self, event: str, payload: dict, secret: str = "test-secret") -> object:
        body = json.dumps(payload).encode("utf-8")
        headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
        if secret:
            digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Hub-Signature-256"] = f"sha256={digest}"
        return self.client.post("/github/webhook", data=body, headers=headers)

    def test_normalize_pull_request(self) -> None:
        notif = normalize_github_event("pull_request", PR_PAYLOAD)
        assert notif is not None
        self.assertEqual(notif.id, "github-pull_request-456")
        self.assertEqual(notif.author, "Sarah")
        self.assertEqual(notif.type, "pull_request")

    def test_normalize_issue_and_comment(self) -> None:
        issue = normalize_github_event("issues", ISSUE_PAYLOAD)
        comment = normalize_github_event("issue_comment", COMMENT_PAYLOAD)
        assert issue is not None and comment is not None
        self.assertEqual(issue.id, "github-issue-789")
        self.assertEqual(comment.type, "mention")
        self.assertEqual(comment.author, "Alice")

    def test_webhook_queues_pr(self) -> None:
        resp = self._post("pull_request", PR_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.queue.get_pending()), 1)
        self.assertEqual(self.queue.get_pending()[0].title, "Add OAuth2 support")

    def test_invalid_signature_rejected(self) -> None:
        resp = self._post("pull_request", PR_PAYLOAD, secret="wrong")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.queue.get_pending(), [])

    def test_ping_ok(self) -> None:
        resp = self._post("ping", {"zen": "Keep it logically awesome."})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
