# Go-Live Checklist

Walk this before a demo. Prefer `python scripts/golive.py` over ticking boxes by hand.

## Cold Start

- [ ] Ollama reachable at the configured host (default `http://localhost:11434`)
- [ ] Start `ollama serve` if it is not already running
- [ ] `.env` has real Slack tokens (`xoxb-` / `xapp-`, not placeholders)
- [ ] Daemon webhook port (default `9001`) accepts a connection
- [ ] Start capture + release together (`python -m src.main watch`)

## Dual-Source Capture

- [ ] Slack DM or `@mention` appears as a new `slack` row in `queue.json`
- [ ] GitHub issue / PR / comment appears as a new `github` row in `queue.json`

## Triggers

- [ ] Manual release: `python -m src.main release` shows the TUI (or held message)
- [ ] Focus on: `release` prints “Queue held” and does not dump the table
- [ ] Focus off: gate is clear again
- [ ] Commit trigger: a real commit while `watch` is running releases the queue
- [ ] Timer trigger: `watch --interval-seconds N` releases after N seconds when focus is off

## TUI Interaction

- [ ] Keyboard: `d <n>` defer, `x <n>` dismiss, `o <n>` open source, `q` quit

## Focus Analytics

- [ ] `focus_minutes_protected_today == interruptions_caught_today × avg_context_switch_cost_minutes` (default 17.5)

## Restart Persistence

- [ ] `queue.json` exists and has `notifications`, `settings`, and `stats`
- [ ] Queue contents survive a daemon restart (same notification ids after restart)

## Final Sign-Off

- [ ] Git working tree has no tracked `.env` and no unexpected dirty files under `src/`
- [ ] Definition of Done rows in `IMPLEMENTATION_PLAN.md` can be marked from the summary view
