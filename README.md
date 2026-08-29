# hardened-local-agent

**Offload sensitive or heavy tasks to an autonomous agent that runs entirely on your own
hardware — and stop paying per token for them.**

A privacy-first autonomous agent platform designed around a single hard constraint: *the
content the agent reads must never leave the machine.* Email bodies, documents, account data
— all of it is processed by a local model on a dedicated, egress-locked host. The agent works
a queue of projects asynchronously, asks a clarifying question when it hits a genuine
decision, parks that project, and switches to the next one. You answer from your phone; it
resumes.

The interesting engineering here is not the agent loop. It is the **containment**: the design
assumes the agent *will* be hijacked by the content it reads, and makes that survivable.

```
  Dedicated agent host (16GB Apple Silicon, encrypted, egress-locked)
  |-- local inference server --serves--> mid-size quantized model   <- the brain, on-device
  |-- agent harness --talks to--> http://127.0.0.1:11434/v1         <- LOCAL endpoint only
  |     |-- skills / cross-session memory
  |     +-- minimal toolsets per task (structural capability limits)
  |-- Orchestration: project queue - clarification loop - async scheduler
  |-- Ingestion:     out-of-band read-only mail fetch -> inert local records
  |-- Security:      default-deny egress - least-priv creds - structural approval gates
  +-- Monitoring:    Tier A log -> Tier B alerts -> Tier C digest -> Tier D roll-up

  Private mesh (WireGuard) --> phone dashboard + self-hosted push
                               the approval surface, from anywhere
```

## Why this exists

Two problems, one system.

**Cost.** Long-running, repetitive, or bulk work — triaging a mailbox, summarizing a backlog,
drafting first passes — burns frontier-model tokens on tasks a 14B-class local model handles
fine. Those tasks run here for the price of electricity. Frontier models stay available as a
deliberate, per-question escalation for the genuinely hard ones.

**Privacy.** The tasks most worth automating are the ones you least want in someone else's
training set. Running the model locally is the easy half. The hard half is making sure the
*agent* can't ship the data out either.

## The threat model in one line

Treat the agent as **untrusted the moment it reads an email.**

The realistic attack is not malware on disk. It is **indirect prompt injection**: text inside
a document or message talks the agent into misusing the legitimate access you gave it —
sending, deleting, leaking, or writing itself a new capability. Nothing "infected" ever
appears on the machine; the agent simply acts, with valid credentials, on someone else's
instructions.

Two consequences shape every design decision in this repo:

1. **The model can never be the gate.** A model that can be persuaded into acting can equally
   be persuaded into reporting that nothing happened. Gates must be *structural* — enforced
   by code the agent cannot call, using credentials the agent does not hold.
2. **Detection cannot un-leak data.** So roughly 80% of the protection is preventive (egress
   lock, least-privilege credentials, capability limits) and 20% detective (the monitoring
   tiers). Preventive controls get built first.

See [docs/security-model.md](docs/security-model.md).

## How containment actually works

| Control | What it buys |
|---|---|
| **Default-deny egress firewall** | A fully hijacked agent has nowhere to send anything. Highest-value single control. |
| **Out-of-band ingestion** | A trusted non-agent process fetches mail read-only into inert local files. The agent never holds the mail credential and never makes a network call. |
| **Minimal toolsets per task** | Triage runs with a memory-only toolset — no network tool, no send tool. An injection that succeeds rhetorically still has nothing to execute with. |
| **Structural approval gates** | The agent produces a *draft*; a separate, human-approved path performs any send. Not a harness prompt — headless runs bypass those by design. |
| **Least-privilege credentials** | Read-only OAuth scope, re-verified by the fetcher at startup, which refuses to run on a broader-scoped token. |
| **Deterministic audit** | Every action appended to JSONL and diffed against allowlists. No LLM judges the logs; an LLM may only reformat a digest for human reading. |

## Repo layout

```
docs/
  architecture.md      the whole design, and why each decision went the way it did
  security-model.md    threat model and the control list
  monitoring-tiers.md  the A/B/C/D audit design
  orchestrator.md      project state machine and the async scheduling model
  email-ingestion.md   the out-of-band read-only mail pipeline
  deployment.md        standing the host up, in order, with the firewall last
scripts/
  audit_logger.py      Tier A - append-only JSONL + new-vs-known classification
  alert.py             Tier B - real-time push for high-blast-radius events
  workflow_digest.py   Tier C - per-workflow summary
  nightly_rollup.py    Tier D - trends and anomalies
  daily_digest.py      morning health push that doubles as a liveness alarm
  hub.py               mesh-only API + mobile dashboard (stdlib, no framework)
  gmail_auth.py        one-time read-only OAuth mint (loopback + PKCE, stdlib)
  gmail_fetch.py       read-only fetcher -> inert inbox records
  draft_reply.py       local model drafts a reply; never sends
  orchestrator/
    scheduler.py       single-flight loop: pick, step, persist, repeat
    cloud_ask.py       optional per-question cloud escalation
config/                templates only - no real configuration is committed
allowlists/            known hosts / skills / credential-uses
inbox/  projects/      record schemas and examples
```

## Notable implementation details

- **Stdlib only, everywhere.** The hub, the dashboard, the OAuth flow, the mail client, the
  scheduler — no pip, no framework. On a host with locked-down egress you cannot casually
  `pip install` anything, and every dependency is one more thing that can phone home. The
  Gmail API is plain REST/JSON, so token refresh, list and get are a handful of `urllib`
  calls.
- **Every model-output contract has a deterministic backstop.** Small local models drift from
  formatting contracts: they bullet required markers, wrap fields across lines, and re-ask
  questions that were already answered. The parsers use layered fallbacks, and what ends a
  runaway clarification loop is a hard counter, not a politely-worded prompt.
- **Restricting toolsets does double duty.** It is the security control *and* the performance
  fix: the full tool-schema block is tens of kilobytes reprocessed on every agentic turn, and
  on a laptop-class host that dominates step latency.
- **Mode switching without redeploying.** `config/mode.env` decides whether the scheduler
  drives the queue or idles. One-line change, no service restart.
- **Graceful degradation on escalation.** A cloud ask falls back down the chain
  claude → chatgpt → local model, and the answer carries its failure trail, so a plan limit
  or a missing CLI degrades rather than fails.
- **The absence of a signal is a signal.** The daily digest is scheduled so that a *missing*
  push is the alarm — no external watchdog needed to notice a dead host.

## Status

A personal reference implementation and design document, published as a portfolio piece. The
code here is the real implementation; the docs describe the architecture and the reasoning
behind each decision. It is not packaged for general use: standing it up means following
[docs/deployment.md](docs/deployment.md) on a host you control, and the security posture
depends entirely on the host-level controls described there actually being in place.

Built on the [Hermes Agent](https://github.com/NousResearch/hermes-agent) harness and an
OpenAI-compatible local inference server. Both are swappable — the harness is invoked as a
subprocess behind one constant, and the model endpoint is one line of config.

## License

MIT — see [LICENSE](LICENSE).
