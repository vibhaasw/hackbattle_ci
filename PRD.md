# Context Interrupter — Product Requirements Document (PRD)

**Status:** Draft v2 (expanded scope, 36-hour build)
**Owner:** [team]
**Last updated:** [date]

---

## 1. Problem Statement

Developers lose focus constantly to unstructured, real-time notifications from tools like GitHub, Slack, and email. Each interruption costs 15–20 minutes of regained mental state, adding up to roughly 2 hours of lost productive time per 8-hour workday. Existing solutions fail because they either hide information entirely (Do Not Disturb, turning off notifications) or still interrupt at random, uncontrolled moments (manual batch-checking, notification filtering).

**The core insight:** the problem isn't notifications — it's *when* and *how* they arrive. Uncontrolled timing, not information volume, is what breaks focus.

## 2. Vision / Solution Summary

Context Interrupter is a background agent that:
1. **Captures** notifications from multiple developer tools (GitHub, Slack, and optionally Email) as they happen.
2. **Summarizes** each one into a single readable line using a local LLM, tagged with an urgency level.
3. **Holds** them in a queue instead of surfacing immediately.
4. **Releases** the queue only at natural context-switching moments — after a commit, after a successful build, on a timer, when the developer isn't in a meeting, or on manual request.
5. **Displays** the queue in a terminal UI (and optionally a lightweight web dashboard) so the developer can triage in seconds and return to work.

The goal is zero missed information, zero random interruptions.

## 3. Target User

Individual developers (or small teams) doing focused, heads-down technical work who are wired into multiple notification sources (GitHub, Slack, email) and currently rely on willpower or blunt DND tools to protect focus time.

## 4. Goals & Non-Goals

### Goals
- Eliminate real-time interruption from GitHub and Slack notifications without losing any information.
- Make each notification digestible in ~5 seconds via LLM summarization + urgency tagging.
- Release notifications only at moments that don't cost extra context-switching (post-commit, post-build, timer, non-meeting windows).
- Prove the concept is extensible beyond GitHub within the same architecture (Slack live in this build; Email/Linear as an additional source if time allows).
- Give the user a visible, quantified sense of the time/focus they're protecting (focus analytics).

### Non-Goals (this build)
- Full historical notification search/archive.
- Cross-device sync or multi-user/team deployment.
- Replacing Slack/GitHub's native notification settings — this sits alongside them, not instead of them (webhook/API based capture).
- Mobile app or browser extension.
- Persistent database infrastructure (SQLite/JSON is sufficient).

## 5. Feature Set

### 5.1 Core (must-ship)
| Feature | Description |
|---|---|
| GitHub capture | Webhook + API polling for PRs, issues, mentions |
| LLM summarization | Local Ollama call producing a ≤15-word summary per notification |
| Notification queue | JSON/SQLite-backed queue with dedup, read/unread state |
| Release logic | Event-based (git commit, build success) + time-based + manual trigger |
| Terminal UI (TUI) | Displays queued items, keyboard navigation, defer/dismiss/open source |

### 5.2 Expanded scope (this 36-hour build)
| Feature | Description | Why it matters |
|---|---|---|
| Slack integration | Second live notification source (DMs/mentions via Events API or socket mode) | Proves the architecture generalizes beyond GitHub — directly addresses "GitHub-only won't impress judges" |
| Priority/urgency scoring | Same LLM call extended to tag each notification `urgent \| normal \| low` based on content and keywords | Shows AI depth beyond text compression — the agent understands importance, not just summarizes |
| Focus analytics | Tracks interruptions caught + release events; computes an estimated "focus-minutes protected" stat | Gives judges (and users) a concrete, memorable number instead of an abstract claim |
| Calendar-aware release logic | Checks free/busy status before releasing the queue; suppresses release during meetings | Extends release logic meaningfully — avoids the tool becoming its own interruption |
| Priority-based TUI coloring | Urgent = red, normal = default, low = dim, via Rich styling | Low-effort visual payoff that communicates the urgency feature at a glance |

### 5.3 Stretch (one, if time allows)
Pick **one**:
- **Email integration** (IMAP polling on a labeled inbox) — completes the 3-source story from the original problem scenario.
- **Lightweight web dashboard** (Flask/FastAPI + single HTML page) mirroring the TUI — better for remote/projector demos than a terminal window.

## 6. User Stories

- As a developer, I want GitHub and Slack notifications to stop popping up mid-task, so that I can stay in flow.
- As a developer, I want to see a short, clear summary of what each notification is about, so that I don't need to open the source to know if it matters.
- As a developer, I want urgent items visually distinguished from routine ones, so that I can decide fast whether something needs immediate attention.
- As a developer, I want notifications released only when I've naturally paused (after a commit, after a build, or on a timer), so that the tool itself never interrupts me.
- As a developer, I don't want the queue to pop up while I'm in a meeting.
- As a developer, I want to see how much focus time I've protected today, so that I have evidence the tool is working.

## 7. Success Criteria

### Hackathon / Demo
1. End-to-end flow works live: a real GitHub PR and a real Slack message are captured, summarized, queued, and released together at a demo-triggered moment.
2. Urgency tagging visibly differentiates at least one "urgent" and one "normal" item in the demo queue.
3. The focus-analytics number is shown on screen as part of the closing pitch.
4. Judges can articulate, unprompted, why this reduces context-switching (i.e., the demo makes the mechanism obvious, not just the output).

### Post-hackathon (directional, not scored)
- Measurable reduction in self-reported interruptions over a week of real use.
- Adding a new source (e.g., Linear) takes under 2 hours, validating the architecture's extensibility claim.

## 8. Risks & Assumptions

| Risk | Mitigation |
|---|---|
| Local LLM (Ollama) is slow or unavailable during demo | Fallback formatter: `[TYPE] AUTHOR: TITLE` with no LLM call |
| Slack Events API setup/auth eats into build time | Use Socket Mode for zero public-URL requirement |
| Calendar API auth adds unplanned complexity | Fall back to a manual "focus mode" toggle if OAuth setup stalls |
| Live demo webhook delivery fails (network/ngrok) | Keep `demo/test_notifications.json` fallback to replay canned events |

## 9. Open Questions
- Do we need real Google Calendar OAuth, or is a manual toggle acceptable for demo purposes given time constraints?
- Is Slack Socket Mode acceptable, or does the judging criteria expect a public webhook?
