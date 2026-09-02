# Orchestration layer

The orchestrator is what lets the agent work through a queue unattended instead of waiting for
you to drive it. It is implemented in `scripts/orchestrator/scheduler.py`.

## Purpose

You hand the agent a project list. It works each project until it reaches a decision it cannot
make on its own, asks a single question, parks that project, and moves to the next actionable
one. Your answer un-parks it.

What this saves is waiting time rather than compute. It is a driver on top of the rest of the
stack, not a replacement for it, and it stays decoupled so that `default` mode with no
orchestrator running still works. The mode toggle is in `config/mode.env`.

## Cooperative async, not parallel compute

A single-GPU host runs one inference at a time. When a project blocks on human input it is
using no compute, so the scheduler picks up the next actionable project instead of idling.
Only one step runs at a time, enforced with an exclusive lock file, since two schedulers
working on the same records would interleave writes and corrupt state.

Real parallelism would need a second worker on separate hardware. That is out of scope, and
generally not the limiting factor.

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

Flat JSON files were chosen over a database. The state machine is testable without the model,
the hub and the scheduler share one representation with no schema layer in between, and
inspecting or repairing state is straightforward. A queue this size does not need more than
that.

## Scheduling priority

The loop drains work in this order:

1. **Asks** — single-turn questions from the dashboard. Someone is waiting on a reply, so these
   run before anything else.
2. **Projects** — answered clarifications first, then fresh projects by priority. A project
   whose question you have already answered should not wait behind a new one.
3. **Ambient work** — email triage and similar background tasks, only when nothing above is
   pending. It fills idle time without competing with a waiting person.

## Contract with the model, and its backstops

Each step gets a self-contained prompt containing the title, the notes so far and the full Q&A
history, and must reply in a fixed format: a `STATUS:` line, then either the deliverable or a
`QUESTION:` line.

Small models follow this loosely, so each of the following handles a specific way the contract
was broken during development:

- **Marker parsing with three fallbacks:** `STATUS:` on the first line, then `QUESTION:`
  anywhere, then a trailing-question-mark heuristic. An unmarked question must not be recorded
  as "done", since that ships an unfinished deliverable and closes the project.
- **List-bullet tolerance:** models emit `- STATUS:` and `* QUESTION:` under formatting
  pressure, so bullet prefixes are stripped before matching.
- **A clarification budget:** a hard cap on questions per project. Models re-ask questions that
  were already answered and will otherwise loop. The prompt states the budget and says
  re-asking counts as a failure, but the counter is what stops it. Once the budget is spent the
  scheduler ships whatever exists.
- **Blank-line termination for wrapped fields:** a multi-line draft field ends at the first
  blank line, so a trailing reasoning block appended by the model is not swallowed into the
  payload.

The general point is that a prompt is a request and the parser is what enforces it. Assume
every model-output contract needs a deterministic backstop.

## Failure handling

- **Every terminal outcome sends a push.** Done, blocked and needs-input all notify. An earlier
  version pushed only on success and questions, which meant a blocked project failed silently
  and looked identical to a system with nothing to do.
- **Timeouts are outcomes rather than crashes.** A step that exceeds its wall clock marks the
  project `blocked` and records the log path in `notes`, instead of leaving the record stuck
  mid-flight.
- **Full output is kept.** The dashboard shows a truncated excerpt; the complete step output
  including stderr goes to `logs/orchestrator/<step-id>.md`.
- **Cloud failures do not kill the loop.** Escalation errors are caught broadly and turned into
  a fallback, since a scheduler that exits on a network error also stops all unrelated work.

## Integration with gates and monitoring

- Every orchestrator action goes through `audit_logger.log_event(...)`, which is Tier A.
- A clarification question is visible at Tiers B and C, so design decisions and operational
  actions appear in the same stream.
- No project advances past a design decision without a human answer. This is the same
  human-in-the-loop gate as "approve this send", applied to direction rather than to an action.

## Mode toggle

The scheduler only acts in `orchestrator` mode. In `default` mode it idles and re-reads the
mode file, so changing one line switches behavior without touching the service manager. The
same file works as a stop control: setting `MODE=default` ends autonomous work after the
current step.
