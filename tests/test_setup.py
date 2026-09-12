"""Setup wizard: env upsert, webhook dedup, and Slack error messages."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.setup_wizard import (
    SetupError,
    configure_from_values,
    ensure_webhook_secret,
    find_hook_by_url,
    format_slack_api_error,
    load_env_file,
    mask_secret,
    normalize_webhook_url,
    parse_repo,
    run_setup,
    slack_prefix_error,
    upsert_env,
    validate_app_token,
    validate_bot_token,
    webhook_urls_match,
)


class ParseAndNormalizeTests(unittest.TestCase):
    def test_parse_repo(self) -> None:
        self.assertEqual(parse_repo("vibhaasw/hackbattle_ci"), ("vibhaasw", "hackbattle_ci"))
        self.assertEqual(
            parse_repo("https://github.com/vibhaasw/hackbattle_ci.git"),
            ("vibhaasw", "hackbattle_ci"),
        )
        with self.assertRaises(SetupError):
            parse_repo("just-owner")
        with self.assertRaises(SetupError):
            parse_repo("a/b/c")

    def test_normalize_webhook_url(self) -> None:
        origin = "https://abc.ngrok-free.dev"
        full = "https://abc.ngrok-free.dev/github/webhook"
        self.assertEqual(normalize_webhook_url(origin), full)
        self.assertEqual(normalize_webhook_url(full + "/"), full)
        self.assertTrue(webhook_urls_match(origin, full))
        with self.assertRaises(SetupError):
            normalize_webhook_url("http://abc.ngrok-free.dev")


class EnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"setup_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / ".env"

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def test_upsert_preserves_unrelated_keys_and_comments(self) -> None:
        self.path.write_text(
            "# keep this comment\n"
            "OLLAMA_MODEL=neural-chat:latest\n"
            "GITHUB_TOKEN=old-token\n"
            "SLACK_TEST_CHANNEL_ID=C123\n",
            encoding="utf-8",
        )
        upsert_env(
            self.path,
            {
                "GITHUB_TOKEN": "new-token",
                "GITHUB_REPO": "acme/demo",
                "SLACK_BOT_TOKEN": "xoxb-live",
            },
        )
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", text)
        self.assertIn("OLLAMA_MODEL=neural-chat:latest", text)
        self.assertIn("SLACK_TEST_CHANNEL_ID=C123", text)
        self.assertIn("GITHUB_TOKEN=new-token", text)
        self.assertNotIn("GITHUB_TOKEN=old-token", text)
        self.assertIn("GITHUB_REPO=acme/demo", text)
        self.assertEqual(load_env_file(self.path)["SLACK_BOT_TOKEN"], "xoxb-live")

    def test_secret_reuse_and_generate(self) -> None:
        reused, generated = ensure_webhook_secret("abc123")
        self.assertEqual(reused, "abc123")
        self.assertFalse(generated)
        fresh, was_new = ensure_webhook_secret("replace-me")
        self.assertTrue(was_new)
        self.assertEqual(len(fresh), 64)
        self.assertNotEqual(fresh, "replace-me")


class SlackErrorTests(unittest.TestCase):
    def test_prefix_errors(self) -> None:
        self.assertIn("empty", slack_prefix_error("SLACK_BOT_TOKEN", "", "xoxb-") or "")
        self.assertIn("wrong prefix", slack_prefix_error("SLACK_BOT_TOKEN", "xapp-nope", "xoxb-") or "")
        self.assertIn("placeholder", slack_prefix_error("SLACK_BOT_TOKEN", "xoxb-replace-me", "xoxb-") or "")
        self.assertIsNone(slack_prefix_error("SLACK_BOT_TOKEN", "xoxb-live", "xoxb-"))

    def test_api_error_hints(self) -> None:
        self.assertIn("unauthorized", format_slack_api_error("invalid_auth"))
        self.assertIn("revoked", format_slack_api_error("token_revoked"))
        self.assertIn("connections:write", format_slack_api_error("missing_scope"))

    def test_validate_bot_rejects_prefix_before_http(self) -> None:
        with self.assertRaises(SetupError) as ctx:
            validate_bot_token("xapp-wrong-kind")
        self.assertIn("wrong prefix", str(ctx.exception))

    def test_validate_app_maps_unauthorized(self) -> None:
        with patch("src.setup_wizard.slack_api", return_value={"ok": False, "error": "invalid_auth"}):
            with self.assertRaises(SetupError) as ctx:
                validate_app_token("xapp-dead-token")
        self.assertIn("unauthorized", str(ctx.exception))
        self.assertIn("invalid_auth", str(ctx.exception))


class WizardFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(__file__).resolve().parent / ".tmp" / f"wiz_{id(self)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / ".env"
        self.path.write_text(
            "OLLAMA_MODEL=keep-me\n"
            "SLACK_TEST_CHANNEL_ID=C999\n"
            "GITHUB_WEBHOOK_SECRET=existing-secret\n",
            encoding="utf-8",
        )
        self.hooks: list[dict] = []
        self.creates = 0
        self.updates = 0
        self.lines: list[str] = []

    def tearDown(self) -> None:
        for path in self.dir.glob("*"):
            path.unlink()
        if self.dir.exists():
            self.dir.rmdir()

    def _create(self, owner, repo, token, *, webhook_url, secret):
        self.creates += 1
        hook = {"id": 101, "config": {"url": webhook_url}, "events": ["issues"]}
        self.hooks.append(hook)
        return hook

    def _update(self, owner, repo, token, hook_id, *, webhook_url, secret):
        self.updates += 1
        return {"id": hook_id, "config": {"url": webhook_url}}

    def _run(self, answers: list[str], *, ngrok: str | None = None) -> object:
        leftover = list(answers)

        def take(_prompt: str) -> str:
            if not leftover:
                raise AssertionError(f"unexpected extra prompt: {_prompt}")
            return leftover.pop(0)

        result = run_setup(
            env_path=self.path,
            input_fn=take,
            getpass_fn=take,
            print_fn=self.lines.append,
            get_repo_fn=lambda *_a: {"full_name": "acme/demo"},
            list_hooks_fn=lambda *_a: list(self.hooks),
            create_hook_fn=self._create,
            update_hook_fn=self._update,
            validate_bot_fn=lambda _t: {"ok": True, "team": "HackBattle"},
            validate_app_fn=lambda _t: {"ok": True, "url": "wss://wss-primary.slack.com/link/x"},
            detect_ngrok_fn=lambda: ngrok,
            port_in_use_fn=lambda _p: False,
        )
        self.assertEqual(leftover, [])
        return result

    def test_github_403_reprompts_for_a_new_pat(self) -> None:
        def get_repo(owner, repo, token):
            if token == "bad-pat":
                raise SetupError("GitHub denied access (403) while trying to read the repository.")
            return {"full_name": f"{owner}/{repo}"}

        leftover = [
            "acme/demo",
            "bad-pat",
            "good-pat",
            "https://abc.ngrok-free.dev",
            "xoxb-bot",
            "xapp-app",
            "n",
        ]

        def take(_prompt: str) -> str:
            return leftover.pop(0)

        result = run_setup(
            env_path=self.path,
            input_fn=take,
            getpass_fn=take,
            print_fn=self.lines.append,
            get_repo_fn=get_repo,
            list_hooks_fn=lambda *_a: [],
            create_hook_fn=self._create,
            update_hook_fn=self._update,
            validate_bot_fn=lambda _t: {"ok": True, "team": "HackBattle"},
            validate_app_fn=lambda _t: {"ok": True, "url": "wss://example"},
            detect_ngrok_fn=lambda: None,
            port_in_use_fn=lambda _p: False,
        )
        self.assertEqual(leftover, [])
        self.assertEqual(result.webhook_action, "created")
        self.assertIn("403", "\n".join(self.lines))
        self.assertEqual(load_env_file(self.path)["GITHUB_TOKEN"], "good-pat")

    def test_creates_hook_and_writes_env(self) -> None:
        result = self._run(
            [
                "acme/demo",
                "github_pat_test",
                "https://abc.ngrok-free.dev",
                "xoxb-bot",
                "xapp-app",
                "n",
            ]
        )
        self.assertEqual(result.webhook_action, "created")
        self.assertEqual(result.webhook_id, 101)
        self.assertEqual(result.slack_workspace, "HackBattle")
        self.assertFalse(result.start_watch)
        self.assertEqual(self.creates, 1)
        env = load_env_file(self.path)
        self.assertEqual(env["OLLAMA_MODEL"], "keep-me")
        self.assertEqual(env["SLACK_TEST_CHANNEL_ID"], "C999")
        self.assertEqual(env["GITHUB_REPO"], "acme/demo")
        self.assertEqual(env["GITHUB_WEBHOOK_SECRET"], "existing-secret")
        self.assertTrue(env["GITHUB_WEBHOOK_URL"].endswith("/github/webhook"))

    def test_existing_hook_offers_update(self) -> None:
        url = "https://abc.ngrok-free.dev/github/webhook"
        self.hooks.append({"id": 55, "config": {"url": url}})
        result = self._run(
            [
                "acme/demo",
                "github_pat_test",
                url,
                "y",
                "xoxb-bot",
                "xapp-app",
                "n",
            ]
        )
        self.assertEqual(result.webhook_action, "updated")
        self.assertEqual(result.webhook_id, 55)
        self.assertEqual(self.creates, 0)
        self.assertEqual(self.updates, 1)

    def test_existing_hook_skip_does_not_duplicate(self) -> None:
        url = "https://abc.ngrok-free.dev/github/webhook"
        self.hooks.append({"id": 55, "config": {"url": url}})
        result = self._run(
            [
                "acme/demo",
                "github_pat_test",
                "https://abc.ngrok-free.dev",
                "n",
                "xoxb-bot",
                "xapp-app",
                "n",
            ]
        )
        self.assertEqual(result.webhook_action, "reused")
        self.assertEqual(self.creates, 0)
        self.assertEqual(self.updates, 0)
        self.assertEqual(len(self.hooks), 1)

    def test_find_hook_by_url(self) -> None:
        hooks = [
            {"id": 1, "config": {"url": "https://other.example/github/webhook"}},
            {"id": 2, "config": {"url": "https://abc.ngrok-free.dev/github/webhook"}},
        ]
        found = find_hook_by_url(hooks, "https://abc.ngrok-free.dev")
        assert found is not None
        self.assertEqual(found["id"], 2)

    def test_configure_from_values_writes_env_and_syncs_hook(self) -> None:
        with (
            patch("src.setup_wizard.validate_bot_token", return_value={"team": "WS"}),
            patch("src.setup_wizard.validate_app_token", return_value={"ok": True}),
            patch("src.setup_wizard.detect_ngrok_https_url", return_value="https://abc.ngrok-free.dev"),
            patch("src.setup_wizard.get_repo", return_value={"full_name": "acme/demo"}),
            patch("src.setup_wizard.list_hooks", return_value=[]),
            patch(
                "src.setup_wizard.create_hook",
                return_value={"id": 42, "config": {"url": "https://abc.ngrok-free.dev/github/webhook"}},
            ),
        ):
            result = configure_from_values(
                github_repo="acme/demo",
                github_token="tok",
                slack_bot_token="xoxb-bot",
                slack_app_token="xapp-app",
                env_path=self.path,
            )
        self.assertEqual(result.webhook_id, 42)
        self.assertEqual(result.webhook_action, "created")
        self.assertEqual(result.slack_workspace, "WS")
        env = load_env_file(self.path)
        self.assertEqual(env["GITHUB_REPO"], "acme/demo")
        self.assertEqual(env["OLLAMA_MODEL"], "keep-me")

    def test_mask_secret(self) -> None:
        self.assertIn("…", mask_secret("github_pat_abcdefghijklmnop"))


if __name__ == "__main__":
    unittest.main()
