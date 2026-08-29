# Orchestration layer

The driver that turns "an agent you talk to" into "an agent that works a queue while you are
asleep." Implemented in `scripts/orchestrator/scheduler.py`.

## Purpose

Hand the agent a project list. It works each one, hits a genuine design decision, asks a
single question, **parks** that project, and switches to the next actionable one. Your answer
un-parks it.

This multiplies output by removing **human decision latency** as the bottleneck — not by
adding compute.

The orchestrator is a **driver on top of** the rest of the stack, not a replacement for it. It
stays decoupled so that `default` mode — no orchestrator at all — always works. See the mode
toggle in `config/mode.env`.

## Reality check: cooperative async, not parallel compute

One inference at a time on a single-GPU host. When a project blocks on human input, **no
compute is in use**, so the scheduler picks up the next actionable project. Single-flight by
design, enforced with an exclusive lock file — two schedulers racing over the same records
would interleave writes and corrupt state.

True parallelism would need a second worker on separate hardware. Out of scope, and mostly not
the constraint that matters.

## State machine

```
  researching --> awaiting-input --(human answers)--> in-progress --> done
       |                                                   |
       +------------------- blocked <---------------------+
                            (error / missing dependency; needs attention)
```

Each project record is one JSON file in `projects/`:

```json
{
  "id": "example-weekly-report",
  "title": "...",
  "state": "researching | awaiting-input | in-progress | blocked | done",
  "priority": 1,
  "clarifications": [{"q": "...", "a": "...", "asked": "...", "answered": "..."}],
  "notes": "...",
  "updated": "ISO-8601"
}
```

Flat JSON files on disk, deliberately: the state machine is fully testable without the model,
the hub and the scheduler share one representation with no schema layer between them, and when
something goes wrong the debugging tool is `cat`.

## Scheduling priority

The loop drains work in this order, and the ordering encodes who is waiting:

1. **Asks** — single-turn questions from the dashboard. A human is actively holding a phone
   waiting for this one, so it preempts everything.
2. **Projects** — answered clarifications first (a human already did their part; do not make
   them wait twice), then fresh projects by priority.
3. **Ambient work** — email triage and similar background tasks, only when nothing above is
   pending. It fills idle time and never competes with a waiting human.

## Contract with the model, and its backstops

Each step gets a self-contained prompt (title, notes so far, full Q&A history) and must reply
in a fixed format: a `STATUS:` line, then either the deliverable or a `QUESTION:` line.

Small models honor this loosely. Every layer below is there because the contract was broken in
practice:

- **Marker parsing with three fallbacks** — `STATUS:` on the first line, then `QUESTION:`
  anywhere, then a trailing-question-mark heuristic. An unmarked question must never be
  silently filed as "done", because that ships an unfinished deliverable and closes the loop.
- **List-bullet tolerance** — models under formatting pressure emit `- STATUS:` and
  `* QUESTION:`. Strip bullet prefixes before matching.
- **A clarification budget** — a hard cap on questions per project. Models re-ask questions
  that were explicitly answered, and will loop indefinitely. The prompt states the budget and
  says re-asking is a failure; the *counter* is what actually stops it. When the budget is
  spent, the scheduler force-ships whatever exists.
- **Blank-line termination for wrapped fields** — a multi-line draft field ends at the first
  blank line, so a trailing "reasoning" block the model appends does not get swallowed into
  the payload.

The general rule: **assume every model-output contract needs a deterministic backstop.** The
prompt is a request; the parser is the enforcement.

## Failure handling

- **Every terminal outcome pushes.** Done, blocked, and needs-input all notify. An early
  version pushed only on success and questions, which meant a blocked project failed
  *silently* — the worst possible failure mode for an unattended system, because it looks
  exactly like a system with nothing to do.
- **Timeouts are outcomes, not crashes.** A step that exceeds its wall clock marks the project
  `blocked` with the log path in `notes`, rather than leaving a record stuck mid-flight.
- **Full output is always kept.** The dashboard shows a truncated excerpt; the complete step
  output, including stderr, goes to `logs/orchestrator/<step-id>.md`.
- **Cloud failures never kill the loop.** Escalation errors are caught broadly and turned into
  a fallback, because a scheduler that dies on a network hiccup stops all unrelated work too.

## Integration with gates and monitoring (non-negotiable)

- Every orchestrator action goes through `audit_logger.log_event(...)` — Tier A.
- A clarification question is a Tier B/C-visible event, so design decisions and operational
  actions surface in one stream.
- **No project advances past a design decision without a human answer.** This is the
  human-in-the-loop gate, generalized from "approve this send" to "approve this direction."

## Mode toggle

The scheduler only acts in `orchestrator` mode; in `default` mode it idles and re-checks the
mode file, so flipping one line hot-swaps behavior without touching the service manager. That
also makes the mode file a kill switch: set `MODE=default` and autonomous work stops at the
end of the current step.
