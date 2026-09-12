# AGENTS.md — Context Interrupter Build Rules

> Place this file at the repo root as `AGENTS.md` (or `.cursorrules` depending on your Cursor version — some setups read both). This is persistent context: read it in full before making any change, and re-check it whenever a decision feels ambiguous.

---

## 0. What This Project Is

Context Interrupter is a background daemon that captures developer notifications (GitHub, Slack), summarizes and urgency-tags them with a local LLM (Ollama), holds them in a queue, and releases them only at natural context-switch moments (commit, build success, timer, or when the developer isn't in a meeting). A terminal UI displays the queue.

Full specs live in the repo at:
- `PRD.md` — product scope, features, success criteria
- `TRD.md` — architecture, components, data models, non-functional requirements
- `IMPLEMENTATION_PLAN.md` — phased build order, checkpoints, cut lines
- `TECHNICAL_ARCHITECTURE.md` — original architecture reference

**Before writing any code in a session, re-read the relevant section of these docs rather than relying on memory of earlier chat context.** If something you're about to build contradicts these docs, stop and flag it instead of proceeding.

---

## 1. Non-Negotiable Architectural Principles

1. **Source-agnostic core.** Every notification source (GitHub, Slack, Email if built) must normalize into the same `Notification` object defined in TRD §5. The queue, summarizer, and release logic must never contain source-specific branching — that logic belongs in each listener.
2. **Separation of data from presentation.** The queue/state manager owns data. The TUI (and any future web dashboard) is a pure renderer of that data. This is critical: **a UI/UX teammate will be reskinning the TUI/dashboard separately**, so:
   - Never hardcode display strings, colors, or layout inside `queue.py`, `notification.py`, `summarizer.py`, or `release_logic.py`.
   - All styling (colors per urgency, panel layout, labels) should live in `tui.py` (or a dedicated `theme.py`/`config.py` section), reading from a small, clearly named config so a designer can change look-and-feel without touching business logic.
   - Expose queue/stats state via a simple function or small API (e.g., `get_queue_snapshot()`, `get_stats_snapshot()`) rather than having the renderer reach into internal data structures directly. This makes it trivial to swap in a designer's TUI theme or a web dashboard later without refactoring the daemon.
3. **Graceful degradation over crashes.** Every external dependency (Ollama, GitHub API, Slack, Calendar API) must have a defined fallback per TRD §6. If a dependency is unavailable, the system should keep functioning in a reduced mode — never crash the daemon or lose a notification.
4. **No scope invention.** Do not add features, sources, or config options beyond what's in PRD.md without flagging it first. If something seems missing or unclear, ask rather than assume — this is a hackathon build with a fixed time budget, and speculative work is time we don't have.
5. **Respect the cut lines.** IMPLEMENTATION_PLAN.md §4 defines what to drop first if time runs short (stretch feature → calendar API → focus analytics → urgency coloring). Never let a "nice to have" block a "must ship" item in PRD §5.1.

---

## 2. Tech Stack (locked — do not substitute without asking)

| Component | Tech |
|---|---|
| Language | Python |
| GitHub | REST API + Webhooks (Flask endpoint) |
| Slack | `slack-bolt`, Socket Mode |
| LLM | Ollama, `neural-chat` model, local REST API |
| Queue store | JSON file (SQLite only if concurrency issues actually appear — don't pre-optimize) |
| Calendar | Google Calendar API `freebusy.query`, OR manual `--focus-mode` toggle if OAuth stalls |
| TUI | `rich` |
| Web dashboard (if built) | Flask or FastAPI, single HTML/JS page |

Do not introduce a different framework, database, or LLM runner without explicit approval — this includes swapping Ollama for a hosted API, or JSON for a "proper" database "for robustness." Time budget doesn't allow re-litigating these decisions mid-build.

---

## 3. File Structure (follow exactly)

```
context-interrupter/
├── README.md
├── requirements.txt
├── .env.example
├── AGENTS.md
├── PRD.md / TRD.md / IMPLEMENTATION_PLAN.md / TECHNICAL_ARCHITECTURE.md
├── src/
│   ├── main.py
│   ├── github_listener.py
│   ├── slack_listener.py
│   ├── email_listener.py        # only if Email is the chosen stretch feature
│   ├── notification.py
│   ├── queue.py
│   ├── summarizer.py             # includes urgency scoring
│   ├── release_logic.py          # includes calendar gate
│   ├── analytics.py              # focus-minutes tracking
│   ├── tui.py
│   ├── dashboard.py               # only if web dashboard is the chosen stretch feature
│   ├── theme.py                   # styling config, isolated for the design teammate
│   └── config.py
├── tests/
│   ├── test_summarizer.py
│   ├── test_queue.py
│   ├── test_github.py
│   ├── test_slack.py
│   └── test_release_logic.py
└── demo/
    └── test_notifications.json
```

Do not restructure this without a strong reason — the phased build plan assumes this layout.

---

## 4. Data Models (copy exactly from TRD §5 — do not redesign)

```json
// Notification
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
  "raw_data": {}
}
```

```json
// Queue state
{
  "notifications": [],
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

---

## 5. Coding Conventions

- Python 3.10+, type hints on all function signatures.
- Docstrings on every public function/class — one or two lines is enough, this isn't a library.
- Fail loudly in logs, fail quietly to the user — log exceptions with context, but never let an unhandled exception kill the daemon process. Wrap external calls (Ollama, GitHub, Slack, Calendar) in try/except with the documented fallback behavior.
- Keep listener modules under ~150 lines each; if a listener grows past that, it's doing too much and normalization logic should move to `notification.py`.
- No global mutable state outside `queue.py`'s state manager.
- Config values (ports, intervals, model name, thresholds) belong in `config.py` or `.env`, never hardcoded inline.

---

## 6. Testing Expectations

- Every listener needs a test using a canned payload (see `demo/test_notifications.json` for shape).
- `summarizer.py` needs a test for both the happy path and the fallback (simulate Ollama being down).
- `release_logic.py` needs a test per trigger type (commit, build, timer, manual, calendar-gated).
- Don't chase 100% coverage — this is a 36-hour build. Prioritize tests that de-risk the live demo (i.e., "what happens if X fails during judging").

---

## 7. Git & Version Control

**Repo:** https://github.com/vibhaasw/hackbattle_ci.git

**Why this section exists:** judges penalize a repo that looks like it was uploaded once at the deadline. The commit history is part of the deliverable — it needs to visibly show iterative, real development across the 36 hours, not one giant dump.

Rules:
1. **Commit early, commit often.** A commit is due after every meaningfully working unit of code — not just at the end of a phase. As a rule of thumb: finishing `notification.py`, finishing `queue.py`, and finishing `github_listener.py` are three separate commits, not one. If you find yourself about to commit 300+ changed lines across five files in one shot, stop and think about how that should've been split.
2. **Commit message format:** Conventional Commits style — `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`. Example: `feat(github): add webhook listener for PR and issue events`. Messages should describe what changed and why in one line; no vague messages like "updates" or "wip".
3. **Never commit secrets.** `.env`, tokens, API keys, and Ollama/Slack/Google credentials must be in `.gitignore` from the very first commit — set this up before writing any code that touches credentials.
4. **Push after every commit** (or at minimum every 2–3 commits) so the remote history reflects real-time progress, not a local backlog pushed all at once later.
5. **Branch strategy:** keep it simple given the time constraint — commit directly to `main` unless something is genuinely experimental/risky, in which case use a short-lived branch (e.g. `feature/calendar-gate`) and merge once verified working.
6. **Tag phase checkpoints.** When a phase's checkpoint (per IMPLEMENTATION_PLAN.md) is verified working, make a clear commit or lightweight git tag marking it, e.g. `git tag phase1-checkpoint` or a commit message like `checkpoint: Phase 1 — GitHub end-to-end verified`. This makes the demo narrative traceable directly in the commit log if a judge looks.
7. **Don't rewrite history.** No force-pushes, no squashing away the incremental commits after the fact — the granular history is the point.

---

## 8. Interaction Protocol for the Agent

- Before starting a new phase, confirm you've re-read the relevant IMPLEMENTATION_PLAN.md phase section.
- If a requirement in PRD/TRD is ambiguous or contradicts something else in the docs, stop and ask rather than guessing.
- When a checkpoint from IMPLEMENTATION_PLAN.md is reached, explicitly state what was verified and what wasn't, so the team can decide whether to proceed or fix first.
- Do not silently change tech stack, file structure, or data models to "improve" them — propose the change and wait for confirmation.
- Since UI/UX is being designed separately: when touching `tui.py` or `dashboard.py`, keep changes to structure/data-wiring only unless explicitly asked to also handle visual design — the design teammate owns final styling decisions in `theme.py`.
