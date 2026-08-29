# Architecture

The whole design in one document, with the reasoning behind each decision. Read
[security-model.md](security-model.md) first if you only read one thing — the threat model is
what forces most of the choices below.

## The constraint

Sensitive content — email bodies, documents, account data — **must never leave the machine**
and must never enter a third-party training set. Everything the agent reads is processed by a
model running on the local host.

That constraint is load-bearing. It rules out cloud model providers for the main loop, it
rules out most hosted automation platforms, and it forces the monitoring, ingestion and
approval design into shapes they would not otherwise take.

## Layers

```
  Dedicated agent host (encrypted, egress-locked, no other purpose)
  |
  |-- BRAIN      local inference server, loopback-only, one mid-size quantized model
  |-- HARNESS    agent framework: skills, cross-session memory, tool execution
  |-- DRIVER     orchestrator: project queue, clarification loop, async scheduler
  |-- INGEST     out-of-band read-only fetchers producing inert local records
  |-- SECURITY   default-deny egress, least-privilege creds, structural gates
  +-- MONITOR    Tier A log -> Tier B alerts -> Tier C digests -> Tier D roll-ups
  |
  +-- MESH       private WireGuard mesh -> phone dashboard + self-hosted push
```

### Brain

A mid-size quantized model (14B-class, 4-bit) served over an OpenAI-compatible endpoint bound
to loopback. Sizing is set by the host's unified memory: roughly 9-10GB of weights plus a
quantized KV cache leaves enough headroom on a 16GB machine to actually run.

Two configuration details matter more than the model choice:

- **Pin the model explicitly and re-verify at startup.** Model launchers offer cloud-hosted
  variants in the same menu as local ones. A single wrong selection silently sends every
  prompt off-device and breaks the entire privacy guarantee — with no error and no log line.
- **Context length is negotiated, not declared.** A harness may require a large runtime
  context for its tool schemas, while the model's own architecture caps it lower and the
  server silently clamps to that cap. Check what the server actually allocated, not what you
  asked for.

Swapping the model is a one-line config change. Swapping the inference server is a URL change.

### Harness

The agent framework provides skills, cross-session memory, and tool execution. It is invoked
as a subprocess behind a single constant (`AGENT_BIN`), in headless one-shot mode: one prompt
in, final text out.

**Prompts are self-contained by design.** The orchestrator does not rely on the harness's
session memory to carry context across steps — smaller models recall prior turns unreliably,
and a step that silently loses context produces confidently wrong work. Instead the project
record carries the full brief plus the complete Q&A history, and every step prompt is
rebuilt from it. This costs prompt tokens, which are free locally, and buys determinism.

### Driver: the orchestrator

The working model: hand the agent a project list. It researches each one, hits a genuine
design decision, asks a single question, **parks** that project, and switches to the next.
When you answer — from your phone, wherever you are — it un-parks and resumes.

The point is not parallel compute. On a single-GPU host there is exactly one inference at a
time; this is **cooperative async task-switching**. What it removes is *human decision
latency*, which is usually the real bottleneck: a project blocked on a question you have not
seen yet is consuming no compute and making no progress, and there is no reason the machine
should sit idle waiting for you.

See [orchestrator.md](orchestrator.md) for the state machine.

### Ingest

Content enters through **out-of-band fetchers**: trusted, non-agent processes that pull data
read-only and write inert records to disk. The agent then reads those files with a minimal
toolset.

This inverts the obvious design — giving the agent a mail tool — for a specific reason: the
agent is assumed to be injectable, so it must never hold a credential or a network tool it
could be talked into using. See [email-ingestion.md](email-ingestion.md).

### Security

Covered in full in [security-model.md](security-model.md). The short version: default-deny
egress, least-privilege credentials, structural approval gates, gated self-modification, a
pinned local model, machine isolation, and minimal per-task toolsets.

### Monitoring

Four tiers, cadence matched to risk: a silent append-only log of everything, real-time alerts
for five high-blast-radius event classes only, per-workflow digests as the primary review
surface, and nightly roll-ups for trends. All detection is deterministic. See
[monitoring-tiers.md](monitoring-tiers.md).

### Mesh and the approval surface

Three devices — a workstation, a phone, and the agent host — joined by a private WireGuard
mesh. Nothing is exposed to the public internet; the coordination service sees connection
metadata, never content.

On the agent host, two services bind the **mesh interface only** (verify with `lsof -i`, never
by assumption): a small stdlib HTTP API serving a mobile-first dashboard, and a self-hosted
push server. The phone adds the dashboard to its home screen.

**The phone is the approval surface, and that is the point of the whole mesh.** An approval
gate and a mobile interface are the same feature: a gated action or a clarifying question
fires a push; tapping it opens the dashboard; you answer or approve from wherever you are;
the agent resumes. Without this, "approval gates" means "the agent stops until you get home,"
and the honest outcome of that is that you eventually turn the gates off.

Design notes that fell out of building it:

- **Being on the mesh is not authentication.** Mutating endpoints require a bearer token; read
  endpoints are open. A device on the mesh is *reachable*, not *authorized*.
- **Self-hosted push only arrives while the phone is on the mesh.** Accepted knowingly, since
  the alternative is routing approval prompts through a third-party cloud — the worst possible
  fit for this threat model. The dashboard shows anything missed.
- **A dashboard that auto-refreshes will eat what you are typing.** Re-rendering the list on a
  timer wipes a half-composed answer and dismisses the phone keyboard. Suppress the re-render
  while any input holds focus or content.

## Two run modes

| Mode | Behavior | How to switch |
|------|----------|---------------|
| `default` | You talk to the agent directly, one task at a time. The scheduler is not driving anything. | `MODE=default` in `config/mode.env` |
| `orchestrator` | The scheduler pulls projects from the queue and juggles them asynchronously. | `MODE=orchestrator` |

Both share the identical brain, harness, security and monitoring — only the driver differs.
The orchestrator is deliberately decoupled so that **default mode always works with it
absent**, and the mode file is read on each loop iteration so switching needs no service
restart.

## Cloud escalation: the deliberate exception

Some tasks genuinely exceed a 14B model. Rather than pretend otherwise, escalation to a
frontier model is a first-class, *narrow*, per-question exception to the local-only rule.

The dashboard's **Ask** surface is deliberately separate from "hand the agent a project": a
question is conversational — pick an agent, ask, get an answer — not a queued multi-step
project. You choose `local` (default, no egress), or a cloud agent.

Its gates:

- **Never autonomous.** An escalation happens only when a human requests it. The agent cannot
  decide on its own to phone out.
- **Content minimization.** Exactly the question the human typed is sent. No local context, no
  files, no history, nothing from the record store. Typing the question and picking a cloud
  agent *is* the approval of precisely that content, because nothing else is attached.
- **No API keys on the host.** Escalation drives already-authenticated subscription CLIs as
  subprocesses. Usage hard-stops at the plan limit instead of billing per token, and there is
  no long-lived key on the agent host to steal.
- **A daily call cap** as a runaway-loop backstop — not a spend cap, since subscription usage
  has no marginal cost, but a looping agent should still hit a wall.
- **Audited and pushed.** Every cloud call is a Tier B event: logged with the external host,
  and pushed to the phone as it happens.
- **Egress-scoped.** The firewall allows exactly those runner binaries to reach exactly those
  API hosts.

**Graceful degradation** matters here more than reliability: the chain falls
`claude → chatgpt → local model`, and the answer is prefixed with the trail of what failed and
why. A plan limit, a missing CLI or a network problem produces a slightly worse answer rather
than an error.

A planned extension, not implemented here: a **sanitization pass** that pseudonymizes
identifying details into placeholders before anything leaves, keeps the mapping local, and
re-hydrates the answer on the host — with the approval screen showing the *exact* outbound
text, so what leaves is verified by eyeball rather than by trust.

## Design principles worth stating explicitly

**Assume every model-output contract will be violated.** Small models bullet required markers,
wrap fields across lines, skip mandatory steps, and re-ask questions that were already
answered. Every parser here has layered fallbacks, and every loop has a deterministic hard
stop. What ends a runaway clarification cycle is a counter, not a sternly-worded prompt.

**Stdlib only.** No pip, no framework, anywhere in this repo. On an egress-locked host you
cannot casually install anything, and every dependency is another process that can phone home
and another supply chain to trust. It costs some convenience and buys a dependency graph that
fits in your head.

**Make absence a signal.** The daily digest fires on a schedule so that a *missing* digest is
the alarm. A dead host cannot tell you it is dead, so the health check has to be something
that stops happening rather than something that starts.

**Write down the reasoning, not just the result.** Every non-obvious decision here — why
ingestion is out-of-band, why the send gate cannot be a harness prompt, why the toolset is
restricted — is a decision that looks arbitrary six months later and gets "simplified" away by
someone who does not know what it was load-bearing for.
