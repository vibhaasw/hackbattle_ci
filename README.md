# Context Interrupter

A local daemon that captures **GitHub** and **Slack** notifications, summarizes them with a local LLM, holds them in a queue, and releases them only when you ask (or when you are not in focus mode). A terminal UI is the only frontend in this build.

This repo is GitHub + Slack + TUI. There is no email source and no web dashboard.

## What you need

- Python 3.10+
- [Ollama](https://ollama.com) with the `neural-chat:latest` model
- A GitHub repo you can add a webhook to (and a public URL such as ngrok for local testing)
- A Slack app with Socket Mode (bot token + app-level token)

## Setup (clean machine)

```bash
git clone https://github.com/vibhaasw/hackbattle_ci.git
cd hackbattle_ci
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

One command starts ngrok (or reuses a running tunnel), points the GitHub webhook at the live public URL, and serves the dashboard + capture listeners:

```bash
.venv/bin/python -m src.main start
```

Open `http://127.0.0.1:9001/` and paste your GitHub PAT + Slack tokens in **Connect**. The backend writes `.env` and updates the webhook — you do not copy-paste the ngrok URL.

### Live judge path (real events only)

```bash
# 1) empty leftover test rows + zero today's stats
.venv/bin/python -m src.main prime

# 2) listeners + dashboard (skip if start is already running)
.venv/bin/python -m src.main start

# 3) open one real GitHub issue (closes it after the queue catches it)
.venv/bin/python scripts/live_demo.py
```

Then hard-refresh **http://127.0.0.1:9001/**. The github pane should show that issue. In Slack, `@mention` the bot — the slack pane updates from Socket Mode, not from canned data.

Or run the terminal setup wizard:

```bash
ngrok http 9001
.venv/bin/python -m src.main setup
```

Or edit `.env` by hand:

```bash
# required for Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# required for real LLM summaries
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=neural-chat:latest

# optional GitHub webhook signature
GITHUB_WEBHOOK_PORT=9001
GITHUB_WEBHOOK_SECRET=replace-me
```

Pull the model:

```bash
ollama pull neural-chat:latest
```

### Slack app

1. [Create an app](https://api.slack.com/apps) from scratch.
2. **Socket Mode** → on → create an app-level token with `connections:write` → `SLACK_APP_TOKEN`.
3. **OAuth & Permissions** bot scopes: `app_mentions:read`, `channels:history`, `im:history` (add `users:read` and `im:read` if you want display names and reliable DMs).
4. **Event Subscriptions** → bot events: `message.im`, `app_mention`.
5. **App Home** → enable the Messages tab so people can DM the bot.
6. Install the app, copy the `xoxb-` bot token, invite the bot to a channel (`/invite @YourBot`).

### GitHub webhook

`python -m src.main setup` creates the hook via the GitHub API (needs a PAT with `admin:repo_hooks`). Manual fallback:

```bash
.venv/bin/python -m src.main watch
# in another terminal:
ngrok http 9001
```

Repo → Settings → Webhooks → Payload URL `https://<ngrok>/github/webhook`, content type `application/json`, events: Issues, Issue comments, Pull requests.

## Run

```bash
# everything: GitHub webhook + Slack Socket Mode + commit/timer release
.venv/bin/python -m src.main watch

# same process (alias)
.venv/bin/python -m src.main daemon

# show the queue (manual release)
.venv/bin/python -m src.main release

# simulate a meeting (queue is held)
.venv/bin/python -m src.main focus on
.venv/bin/python -m src.main release

# meeting over
.venv/bin/python -m src.main focus off
.venv/bin/python -m src.main release

# one-shot override without persisting
.venv/bin/python -m src.main release --focus-mode on
```

Live-source outage during a demo — load canned GitHub + Slack items (urgent and normal):

```bash
.venv/bin/python -m src.main replay
.venv/bin/python -m src.main release
```

Queue state is `queue.json` in the repo root (not committed). If the TUI dies, open that file:

```bash
jq '.notifications[] | {id, source, summary, urgency}' queue.json
```

## 90-second demo

1. **Problem (15s):** notifications break focus; this tool holds them.
2. **Capture (25s):** open a GitHub issue/PR and DM / `@mention` the Slack bot while the daemon is running. Logs show `Queued github-...` and `Queued slack-...`.
3. **Hold (15s):** `python -m src.main focus on` then `release` — table is hidden, “Queue held”.
4. **Release (20s):** `focus off` then `release` — urgency-colored table (urgent red, low dim).
5. **Close (15s):** point at the FOCUS panel (`🛡 N min protected today` = interruptions × 17.5).

If live GitHub/Slack fail, use `replay` and continue from step 3.

## If something is down

| Failure | What happens |
|---|---|
| Ollama down | Queue still fills. Summary becomes `[TYPE] AUTHOR: TITLE`; urgency uses keywords (`prod`, `urgent`, …). |
| Slack tokens missing | Daemon stays up. GitHub webhooks still work. Logs say `Slack listener disabled: ...`. |
| Slack disconnect | Socket Mode auto-reconnects. GitHub is unaffected. Use `replay` if Slack stays down. |
| Broken calendar credentials | Release **fails open** (not treated as a meeting) and the TUI warns. Use `focus on` to hold the queue. |
| Corrupt `queue.json` | File is backed up to `queue.json.bak` and reset. |
| GitHub webhook fails | Use `replay` or `curl` a payload from `demo/test_notifications.json`. |

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

## Development

Interactive go-live checklist (dev/ops only — not part of the product):

```bash
.venv/bin/python scripts/golive.py
```

Automated checks only: `.venv/bin/python scripts/golive.py --auto`. See `GO_LIVE_CHECKLIST.md`.

Repeatable capture smoke test (live Slack mention + synthetic GitHub webhook):

```bash
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/smoke_test.py --github-mode live
```
