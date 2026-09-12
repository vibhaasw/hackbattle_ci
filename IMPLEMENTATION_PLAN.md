# Context Interrupter — Implementation Plan

**Companion to:** PRD.md, TRD.md
**Build window:** 36 hours
**Last updated:** [date]

---

## 1. Guiding Priorities

1. **Get one source fully working end-to-end before adding a second.** A working GitHub-only pipeline beats a half-working two-source pipeline.
2. **Every feature must degrade gracefully.** If Ollama, Slack, or Calendar auth fails mid-demo, the tool should keep working in a reduced mode, not crash (see TRD §6).
3. **Lock the stretch decision (Email vs. Web Dashboard) early** — by end of Phase 3 at the latest — so it doesn't eat into hardening/demo-prep time.

## 2. Phased Timeline

### Phase 1 — Foundation (Hours 0–8)
**Goal:** GitHub notification → summarized → queued → displayed, working end-to-end, even if crude.

- [ ] Repo scaffold per file structure (main.py, github_listener.py, notification.py, queue.py, summarizer.py, release_logic.py, tui.py, config.py)
- [ ] `Notification` class + JSON queue with dedup
- [ ] GitHub webhook listener (Flask on :9001), ngrok tunnel for local testing
- [ ] Ollama running locally, `neural-chat` pulled, basic summarization prompt wired in
- [ ] Manual-trigger release path + minimal Rich TUI table
- [ ] **Checkpoint:** trigger a real GitHub PR/issue, see it summarized and displayed via manual trigger

### Phase 2 — Second Source + AI Depth (Hours 8–16)
**Goal:** Slack live as a second source; summarizer now also tags urgency.

- [ ] Slack App created (Socket Mode, bot token + app-level token, scopes per TRD §3.2)
- [ ] `slack_listener.py` normalizing Slack events into `Notification` objects
- [ ] Extend summarizer prompt to return `{summary, urgency}` JSON; add keyword-based fallback for parse failures
- [ ] Queue schema updated with `urgency` field; sort by urgency then timestamp
- [ ] **Checkpoint:** a real Slack DM/mention and a real GitHub PR both appear in the same queue, each correctly summarized, with an urgency tag

### Phase 3 — Release Intelligence + Visual Polish (Hours 16–22)
**Goal:** Release logic respects meetings; TUI communicates urgency and progress visually.

- [ ] Calendar free/busy check integrated into `should_release()` (Google Calendar API) **or** manual `--focus-mode` toggle if OAuth setup is slow — decide and commit by hour 18
- [ ] Focus analytics: track `interruptions_caught`, `releases`, compute `focus_minutes_protected`; persist to stats store
- [ ] TUI: urgency-based row coloring (red/default/dim) + header stat panel
- [ ] **Decision point:** lock stretch feature (Email integration vs. web dashboard) based on remaining time and how Phases 1–3 went
- [ ] **Checkpoint:** demo a full cycle — notification arrives during a "meeting" (simulated busy calendar) and is correctly held, then releases right after

### Phase 4 — Stretch Feature (Hours 22–30)
**Goal:** Ship the one chosen stretch feature; treat as fully optional if behind schedule.

**If Email:**
- [ ] IMAP polling against a dedicated label/folder
- [ ] Normalize into `Notification`, plug into existing queue/summarizer path (should require no changes to those components — this is the extensibility test)

**If Web Dashboard:**
- [ ] Minimal Flask/FastAPI app reading queue + stats JSON
- [ ] Single HTML page rendering the same info as the TUI, auto-refreshing every few seconds

- [ ] **Checkpoint:** stretch feature demoable in isolation, without depending on last-minute fixes to Phases 1–3

### Phase 5 — Hardening + Demo Prep (Hours 30–36)
**Goal:** No crashes, clean story, rehearsed demo.

- [ ] Walk every row of the failure-modes table (TRD §6) and confirm the mitigation actually works (kill Ollama mid-run, disconnect Slack, revoke calendar token — confirm graceful degradation)
- [ ] Edge cases: missing author, very long titles, empty queue, malformed webhook payload
- [ ] Prepare `demo/test_notifications.json` as a replay fallback in case live webhooks fail during judging
- [ ] Write/finalize README (setup steps, how to run daemon + TUI)
- [ ] Script the 90-second demo narrative: problem → live interruption capture (GitHub + Slack) → meeting-aware hold → release moment → urgency-colored queue → focus-minutes stat as the closing beat
- [ ] Full dry run of the demo, timed

## 3. Definition of Done (updated)

Before presenting to judges:
- [ ] GitHub **and** Slack notifications captured in real-time
- [ ] LLM summarization + urgency tagging working reliably, with fallback tested
- [ ] Queue persists across restarts
- [ ] Release logic respects at least one non-timer trigger live (commit or meeting-gate) in the demo
- [ ] TUI displays cleanly, color-coded by urgency, and is navigable
- [ ] Focus-analytics stat visible and accurate against the demo's own actions
- [ ] Stretch feature (if attempted) works standalone or is cleanly cut from the demo if unstable
- [ ] Full end-to-end test with real notifications from both sources
- [ ] Code is clean and commented
- [ ] README explains setup
- [ ] Demo walkthrough rehearsed at least once end-to-end

## 4. Cut Lines (what to drop first if time runs short)

In order of what to cut if behind schedule:
1. Stretch feature (Email or web dashboard) — cut entirely, keep TUI-only two-source demo
2. Calendar API integration — fall back to manual focus-mode toggle
3. Focus analytics — reduce to a simple counter if the time-estimate math isn't ready
4. Urgency coloring — keep the urgency tag in the summary text even if TUI styling doesn't land

**Never cut:** the core end-to-end pipeline (capture → summarize → queue → release → display) for at least GitHub. That's the floor the whole pitch stands on.
