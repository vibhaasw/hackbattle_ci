"""Offline urgency profiles — no LLM involved."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.notification import Notification
from src.urgency import classify


def _n(**kwargs: object) -> Notification:
    kwargs.setdefault("source", "github")
    kwargs.setdefault("type", "issue")
    kwargs.setdefault("author", "Ada")
    kwargs.setdefault("url", "")
    kwargs.setdefault("raw_id", 1)
    return Notification(**kwargs)  # type: ignore[arg-type]


class UrgencyTests(unittest.TestCase):
    def test_shipping_marks_prod_urgent(self) -> None:
        self.assertEqual(classify(_n(title="Deploy failed in prod"), "shipping"), "urgent")

    def test_shipping_merged_is_low(self) -> None:
        self.assertEqual(classify(_n(title="PR merged by renovate"), "shipping"), "low")

    def test_shipping_plain_pr_is_normal(self) -> None:
        self.assertEqual(
            classify(_n(type="pull_request", title="Add OAuth2 support"), "shipping"),
            "normal",
        )

    def test_oncall_slack_mention_is_urgent(self) -> None:
        notif = _n(source="slack", type="mention", title="when you have a sec")
        self.assertEqual(classify(notif, "oncall"), "urgent")
        self.assertEqual(classify(notif, "shipping"), "normal")

    def test_review_keeps_chat_low(self) -> None:
        chat = _n(source="slack", type="mention", title="standup notes")
        pr = _n(type="pull_request", title="Add rate limits")
        self.assertEqual(classify(chat, "review"), "low")
        self.assertEqual(classify(pr, "review"), "normal")

    def test_quiet_only_incidents(self) -> None:
        self.assertEqual(classify(_n(title="please review this"), "quiet"), "low")
        self.assertEqual(classify(_n(title="prod outage on api"), "quiet"), "urgent")

    def test_unknown_profile_falls_back_to_shipping(self) -> None:
        self.assertEqual(classify(_n(title="Add OAuth2 support"), "nope"), "normal")


if __name__ == "__main__":
    unittest.main()
