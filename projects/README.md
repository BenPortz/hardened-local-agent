# projects/ — the orchestrator work queue

One JSON file per project. The hub writes them when you enqueue from the dashboard; the
scheduler reads, steps, and rewrites them. See
[`docs/orchestrator.md`](../docs/orchestrator.md) for the state machine and
`example-project.json` for the shape.

States: `researching → awaiting-input → in-progress → blocked → done`. The scheduler picks the
highest-priority *actionable* project — `in-progress` (a clarification you just answered) or
`researching` (fresh) — and works it one step at a time.

In `default` mode this folder is ignored entirely; you talk to the agent directly.

Real project records are gitignored, since they contain whatever you handed the agent along
with everything it wrote back.

## Why flat files

The state machine is testable without the model, the hub and the scheduler share one
representation with no schema layer in between, and inspecting or repairing state is
straightforward. Using a database at this size would add a schema change every time a field
moves.
