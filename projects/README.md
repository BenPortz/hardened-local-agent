# projects/ — the orchestrator work queue

One JSON file per project. The hub writes them when you enqueue from the dashboard; the
scheduler reads, steps, and rewrites them. See
[`docs/orchestrator.md`](../docs/orchestrator.md) for the state machine and
`example-project.json` for the shape.

States: `researching → awaiting-input → in-progress → blocked → done`. The scheduler picks the
highest-priority *actionable* project — `in-progress` (a clarification you just answered) or
`researching` (fresh) — and works it one step at a time.

In `default` mode this folder is ignored entirely; you talk to the agent directly.

Real project records are gitignored: they carry whatever you handed the agent, plus everything
it wrote back.

## Why flat files

The state machine is fully testable without the model, the hub and the scheduler share one
representation with no schema layer between them, and when something goes wrong the debugging
tool is `cat`. A queue this size does not need a database, and a database would need a schema
migration every time a field moves.
