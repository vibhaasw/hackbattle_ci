"""Workflow routing from repo / channel / keywords."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings
from src.notification import Notification
from src.queue import NotificationQueue
from src.workflows import assign, default_workflows, ensure_workflows, save_workflows, upsert_repo_workflow


def _settings(tmp: Path) -> Settings:
    base = Settings.load()
    return Settings(**{**base.__dict__, "queue_file_path": tmp / "queue.json"})


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"wf_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.queue = NotificationQueue(_settings(self.dir).queue_file_path, _settings(self.dir))

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def test_repo_url_routes_to_named_workflow(self) -> None:
        rules = [
            {
                "id": "ci",
                "name": "hackbattle",
                "github_repos": ["vibhaasw/hackbattle_ci"],
                "urgency_profile": "shipping",
            },
            {"id": "inbox", "name": "inbox", "catch_all": True},
        ]
        notif = Notification(
            source="github",
            type="issue",
            author="Ada",
            title="live capture",
            url="https://github.com/vibhaasw/hackbattle_ci/issues/2",
            raw_id=2,
        )
        self.assertEqual(assign(notif, rules), "ci")

    def test_unmatched_goes_to_inbox(self) -> None:
        rules = [{"id": "inbox", "name": "inbox", "catch_all": True}]
        notif = Notification(
            source="slack",
            type="dm",
            author="Sam",
            title="hey",
            url="",
            raw_id="x",
        )
        self.assertEqual(assign(notif, rules), "inbox")

    def test_source_panes_catch_github_and_slack(self) -> None:
        rules = default_workflows()
        github = Notification(
            source="github",
            type="issue",
            author="Ada",
            title="n",
            url="https://example.com/1",
            raw_id=1,
        )
        slack = Notification(
            source="slack",
            type="mention",
            author="Sam",
            title="hey",
            url="",
            raw_id="m",
        )
        self.assertEqual(assign(github, rules), "github")
        self.assertEqual(assign(slack, rules), "slack")

    def test_save_retags_pending_when_profile_changes(self) -> None:
        notif = Notification(
            source="slack",
            type="mention",
            author="Riley",
            title="when you have a sec",
            url="",
            raw_id="m1",
            raw_data={"channel": "C-TEST"},
        )
        self.queue.add(notif)
        save_workflows(
            self.queue,
            [
                {
                    "id": "oncall-room",
                    "name": "oncall",
                    "slack_channels": ["C-TEST"],
                    "urgency_profile": "oncall",
                },
                {"id": "inbox", "catch_all": True},
            ],
        )
        stored = self.queue.notifications["slack-mention-m1"]
        self.assertEqual(stored.workflow_id, "oncall-room")
        self.assertEqual(stored.urgency, "urgent")

    def test_upsert_repo_adds_named_pane(self) -> None:
        rows = upsert_repo_workflow(self.queue, "acme/demo")
        ids = [item["id"] for item in rows]
        self.assertIn("acme-demo", ids)
        named = next(item for item in rows if item["id"] == "acme-demo")
        self.assertEqual(named["github_repos"], ["acme/demo"])
        again = upsert_repo_workflow(self.queue, "acme/demo")
        self.assertEqual(sum(1 for item in again if item["id"] == "acme-demo"), 1)

    def test_ensure_seeds_once(self) -> None:
        first = ensure_workflows(self.queue)
        second = ensure_workflows(self.queue)
        self.assertEqual([w["id"] for w in first], [w["id"] for w in second])
        self.assertTrue(any(w.get("catch_all") for w in first))


if __name__ == "__main__":
    unittest.main()
