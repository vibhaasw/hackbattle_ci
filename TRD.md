# Context Interrupter — Technical Requirements Document (TRD)

**Status:** Draft v2 (expanded scope, 36-hour build)
**Companion to:** PRD.md
**Last updated:** [date]

---

## 1. Scope Covered by This Document

This TRD specifies the technical requirements for:
- Core GitHub capture/summarize/queue/release/TUI pipeline
- Slack integration (second source)
- Priority/urgency scoring
- Calendar-aware release logic
- Focus analytics
- Priority-based TUI coloring
- One stretch item (Email integration or web dashboard) — flagged as optional throughout

## 2. System Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                       NOTIFICATION SOURCES                        │
│        GitHub          │          Slack         │  Email (opt.)   │
└───────────┬─────────────────────┬──────────────────────┬──────────┘
            │                     │                      │
            ▼                     ▼                      ▼
┌───────────────────────────────────────────────────────────────────┐
│                   CONTEXT INTERRUPTER DAEMON                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │  Listeners │→ │  Summarizer  │→ │   Queue & State Manager  │   │
│  │ (per source)│  │ + Urgency   │  │  (JSON/SQLite, dedup)    │   │
│  └────────────┘  │   Scoring    │  └────────────┬─────────────┘   │
│                   └──────────────┘               │                 │
│                                                   ▼                 │
│                                     ┌──────────────────────────┐   │
│                                     │      Release Logic        │   │
│                                     │  • git commit             │   │
│                                     │  • build success          │   │
│                                     │  • timer                  │   │
│                                     │  • manual trigger         │   │
│                                     │  • calendar free/busy gate│   │
│                                     └────────────┬───────────────┘   │
│                                                  │                   │
│                                     ┌──────────────────────────┐   │
│                                     │     Focus Analytics       │   │
│                                     │  (tracks releases, est.   │   │
│                                     │   focus-minutes protected)│   │
│                                     └────────────┬───────────────┘   │
└──────────────────────────────────────────────────┼──────────────────┘
                                                    │ IPC / stdout
                                                    ▼
                                     ┌──────────────────────────┐
                                     │        TUI (Rich)         │
                                     │  color-coded by urgency   │
                                     │  + focus-stat panel       │
                                     └────────────┬───────────────┘
                                                  │ (optional)
                                                  ▼
                                     ┌──────────────────────────┐
                                     │  Web Dashboard (stretch)  │
                                     └──────────────────────────┘
```

## 3. Component Specifications

### 3.1 GitHub Listener (existing, unchanged)
- Flask endpoint `/github/webhook` on port `9001`.
- Validates GitHub webhook signature.
- Handles `pull_request`, `issues`, `issue_comment` events.
- Fallback: periodic API polling if webhook delivery fails (per original failure-mode table).

### 3.2 Slack Listener (new)
- **Approach:** Slack Socket Mode (preferred — avoids public URL/ngrok dependency for demo reliability) using `slack-bolt` (Python) or equivalent.
- **Events handled:** `message.im` (DMs), `app_mention`, and channel mentions the bot is invited to.
- **Extracted fields:** sender, channel, message text, permalink.
- Normalizes into the same `Notification` object as GitHub (see 3.5).
- **Auth:** Slack App with Bot Token + App-Level Token (Socket Mode). Scopes: `channels:history`, `im:history`, `app_mentions:read`.

### 3.3 Summarizer + Urgency Scoring (extended)
- Single Ollama call per notification, extended prompt:
  ```
  Summarize this notification in ≤15 words and classify urgency.
  Return JSON: {"summary": "...", "urgency": "urgent|normal|low"}

  Notification: <source, author, title, body>
  ```
- **Urgency heuristic backstop:** if the LLM call fails or returns malformed JSON, apply a keyword fallback (e.g., "urgent", "breaking", "timeout", "blocking", "prod", "@here"/"@channel" in Slack → `urgent`; default → `normal`).
- Cache summaries by notification ID to avoid recomputation on queue reload.

### 3.4 Notification Queue (extended)
- Adds an `urgency` field to the existing schema (see Data Models, section 5).
- Dedup key remains `{source}-{type}-{raw_id}`.
- Sort order for release/display: `urgency desc, timestamp desc`.

### 3.5 Release Logic (extended)
Existing triggers (git commit via `.git/HEAD` mtime, build success via marker file, timer, manual) **plus**:
- **Calendar gate:** before firing any release, query free/busy status.
  - **Preferred:** Google Calendar API `freebusy.query` for the primary calendar, short-lived OAuth token cached locally.
  - **Fallback (if OAuth setup risks the timeline):** a manual `--focus-mode on/off` CLI flag / config toggle that the developer sets when in a meeting.
- Release fires only if `should_release()` is true **and** calendar gate is clear (or focus-mode is off).
- Urgent-tagged items may optionally bypass the calendar gate — **decision needed**, default: no bypass for MVP, all releases respect the gate uniformly to keep logic simple.

### 3.6 Focus Analytics (new)
- On every notification captured: increment `interruptions_caught`.
- On every release event: log a timestamp.
- **Estimated focus-minutes protected** = `interruptions_caught × avg_context_switch_cost_minutes` (configurable constant, default 17.5 based on the 15–20 min research figure), computed on demand for TUI/dashboard display.
- Persisted alongside queue state (e.g., `stats.json` or a `stats` key in the existing queue store) so it survives restarts.

### 3.7 Terminal UI (extended)
- Existing Rich table adds a color column/style based on `urgency`:
  - `urgent` → red/bold
  - `normal` → default
  - `low` → dim
- Adds a header panel showing today's focus-analytics stat (e.g., "🛡 47 min protected today").
- Keyboard controls unchanged (↑↓ navigate, Enter check, d defer, x dismiss, Esc exit).

### 3.8 Web Dashboard (stretch, optional)
- Single-page Flask/FastAPI app reading the same queue/stats store.
- Read-only view for demo purposes (defer/dismiss can remain TUI-only unless time allows POST endpoints).
- No auth needed for local hackathon demo.

### 3.9 Email Listener (stretch, alternative to 3.8)
- IMAP polling (e.g., `imaplib` or `imap-tools`) against a specific label/folder (to avoid ingesting entire inbox).
- Poll interval configurable (default 60s) — no webhook equivalent readily available for most providers within timeframe.
- Normalizes into the same `Notification` object.

## 4. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Webhook receive → queued | < 100ms |
| LLM summarization + urgency tagging | < 5s per notification |
| Calendar free/busy check | < 1s (cached per release cycle, not per notification) |
| TUI render | < 1s |
| Total: notification → available for release | < 6s |
| Daemon uptime during demo | No crashes; all failure modes degrade gracefully per §6 |

## 5. Data Models

### Notification (extended)
```json
{
  "id": "slack-mention-T123-C456-171...",
  "source": "github | slack | email",
  "type": "pull_request | issue | mention | dm",
  "author": "Sarah",
  "title": "Add OAuth2 support",
  "summary": "[PR] Sarah: OAuth2 auth service (3 files, +120 -45)",
  "urgency": "urgent | normal | low",
  "url": "https://...",
  "timestamp": 1694000000,
  "read": false,
  "deferred": false,
  "raw_data": { }
}
```

### Queue State (extended)
```json
{
  "notifications": [ ],
  "settings": {
    "check_interval_seconds": 3600,
    "max_queue_size": 50,
    "auto_clear_after_days": 7,
    "calendar_gate_enabled": true,
    "avg_context_switch_cost_minutes": 17.5
  },
  "stats": {
    "interruptions_caught_today": 0,
    "releases_today": 0,
    "focus_minutes_protected_today": 0
  }
}
```

## 6. Failure Modes & Mitigation (extended from original)

| Failure | Symptom | Mitigation |
|---|---|---|
| Ollama crashes/unavailable | Summaries/urgency fail | Fallback formatter `[TYPE] AUTHOR: TITLE` + keyword-based urgency |
| GitHub webhook timeout | Missing notifications | `python -m src.main replay` against canned `demo/test_notifications.json`. Live GitHub API polling was never built and is not planned. |
| Slack Socket Mode disconnect | Missing Slack notifications | Bolt reconnects automatically. Messages sent during the disconnect window are not replayed — known limitation, not a bug. |
| Calendar API auth failure | Release gate can't evaluate | Default to "not in meeting" (fail-open) so tool never silently stops working, with a visible warning in TUI |
| Queue corruption | JSON parse error | Backup queue.json, auto-repair on load |
| TUI crash | Can't view queue | State remains in JSON, viewable manually |
| Git detection fails | Release never triggers | Manual trigger always available |

## 7. Tech Stack (confirmed)

| Component | Tech |
|---|---|
| Backend/daemon | Python |
| GitHub | GitHub REST API + Webhooks |
| Slack | `slack-bolt` (Socket Mode) |
| Email (if chosen) | `imap-tools` |
| LLM | Ollama (`neural-chat`, fallback `orca-mini` if latency is an issue) |
| Queue store | JSON (SQLite optional if concurrency issues appear) |
| Calendar | Google Calendar API (`freebusy.query`) or manual toggle fallback |
| TUI | Rich |
| Web dashboard (if chosen) | Flask or FastAPI + single HTML/JS page |

## 8. Integration Points Summary

- `POST http://localhost:9001/github/webhook`
- Slack Socket Mode connection (no inbound port needed)
- `POST http://localhost:11434/api/generate` (Ollama)
- `GET https://www.googleapis.com/calendar/v3/freeBusy` (if calendar integration used)
- IMAP host/port per provider (if email integration used)
