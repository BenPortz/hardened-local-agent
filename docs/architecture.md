# Architecture

This is the architechutre document for a automized hermes agent which can handle tasks offloaded from a main work machine. This agent attempts to works securely so it can handle senstive information which you might not want sent to the cloud. 

## The constraint

Sensitive content — email bodies, documents, account data — **must never leave the machine**  
and must never enter a third-party training set. Everything the agent reads is processed by a  
model running on the local host.

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

For my projects brain I used, a mid-size quantized model (14B-class, 4-bit) served over an OpenAI-compatible endpoint bound to loopback. Sizing for this project depends on the amount of ram your workmachine has for a this project roughly 9-10GB of weights plus a quantized KV cache leaves enough headroom on a 16GB machine to actually run.

Two configuration details matter more than the model choice:

- **Pin the model explicitly and re-verify at startup:** This way a model that actually makes sense for your machine.
- **Context length: A harness may require a large runtime context for its tool schemas, while the model's own architecture caps it lower and the server silently clamps to that cap. Check what the server actually allocated, not what you** asked for.

### Harness

The agent framework provides skills, cross-session memory, and tool execution. It is invoked
as a subprocess behind a single constant (`AGENT_BIN`), in headless one-shot mode: one prompt
in, final text out.

**Prompts are self-contained: The orchestrator does not rely on the harness's session memory to carry context across steps. Instead the project record carries the full brief plus the complete Q&A history, and every step prompt is** rebuilt from it. This costs prompt tokens, which are free locally, and which can increase determinism.

### Driver: the orchestrator

You hand the agent a project list. It works each project until it reaches a decision it cannot
make on its own, asks a single question, parks that project, and moves to the next one. Your
answer un-parks it.

A single-GPU host runs one inference at a time, so the scheduler task-switches between
projects. What it saves is waiting time: a project blocked on a question you have not read yet
is using no compute, so the scheduler spends that time on a different project.

See [orchestrator.md](orchestrator.md) for the state machine.

### Ingest

Content enters through out-of-band fetchers: trusted, non-agent processes that pull data
read-only and write inert records to disk. The agent then reads those files with a minimal
toolset.

The credential and the network calls stay with the fetcher. The agent is assumed to be
injectable, so it should not hold a credential or a network tool that could be turned against
it. See [email-ingestion.md](email-ingestion.md).

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

On the agent host, two services bind the mesh interface only: a small stdlib HTTP API serving a
mobile-first dashboard, and a self-hosted push server. Check the bind with `lsof -i` rather
than assuming it. The phone adds the dashboard to its home screen.

The phone is the approval surface, which is the main reason for the mesh. A gated action or a
clarifying question sends a push; opening it loads the dashboard, where you answer or approve,
and the agent continues. Without a remote approval path a gate means the agent waits until you
are back at the machine, and gates that are inconvenient tend to get turned off.

Three notes from building it:

- **Mutating endpoints require a bearer token:** read endpoints are open. Mesh access makes a
device reachable; authorization is a separate check.
- **Self-hosted push only arrives while the phone is on the mesh:** a known limitation, kept
because the alternative is routing approval prompts through a third-party cloud. The dashboard
shows anything that was missed.
- **A dashboard that auto-refreshes will overwrite what you are typing:** re-rendering the list
on a timer wipes a half-composed answer and dismisses the phone keyboard. The fix is to skip
the re-render while any input holds focus or content.



## Two run modes


| Mode           | Behavior                                                                                   | How to switch                       |
| -------------- | ------------------------------------------------------------------------------------------ | ----------------------------------- |
| `default`      | You talk to the agent directly, one task at a time. The scheduler is not driving anything. | `MODE=default` in `config/mode.env` |
| `orchestrator` | The scheduler pulls projects from the queue and juggles them asynchronously.               | `MODE=orchestrator`                 |


Both share the identical brain, harness, security and monitoring — only the driver differs.
The orchestrator is deliberately decoupled so that **default mode always works with it
absent**, and the mode file is read on each loop iteration so switching needs no service
restart.

## Cloud escalation

Some tasks are beyond a 14B model. For those, the system can escalate one question to a
frontier model. This is an exception to the local-only rule, so it is kept narrow and applies
to a single question at a time.

The dashboard's **Ask** surface is separate from handing the agent a project. An ask is a
single question with a single answer: pick an agent, ask, read the reply. You choose `local`
(the default, which sends nothing), or a cloud agent.

The gates on it:

- **Never autonomous:** an escalation happens only when a person requests it. The agent has no
way to initiate one.
- **Content minimization:** only the question as typed is sent. No local context, no files, no
history, nothing from the record store. Because nothing else is attached, typing the question
and choosing a cloud agent is itself the approval of exactly that content.
- **No API keys on the host:** escalation runs already-authenticated subscription CLIs as
subprocesses. Usage stops at the plan limit rather than billing per token, and there is no
long-lived key stored on the agent host.
- **A daily call cap:** a backstop against a runaway loop. Subscription usage has no marginal
cost, so the cap exists to bound call volume rather than spend.
- **Audited and pushed:** every cloud call is a Tier B event, logged with the external host and
pushed to the phone as it happens.
- **Egress-scoped:** the firewall allows only those runner binaries to reach only those API
hosts.

The fallback chain is `claude → chatgpt → local model`, and the answer records which steps
failed and why. A plan limit, a missing CLI or a network problem still produces an answer.

One planned extension is not implemented here: a sanitization pass that replaces identifying
details with placeholders before anything is sent, keeps the mapping local, and restores the
real values in the answer on the host. The approval screen would show the exact outbound text,
so the content leaving the machine can be checked directly.

## Design principles

**Assume every model-output contract will be violated.** Small models bullet required markers,
wrap fields across lines, skip steps, and re-ask questions that were already answered. So every
parser here has layered fallbacks, and every loop has a hard stop. A counter ends a runaway
clarification cycle.

**Stdlib only.** No third-party packages anywhere in this repo. An egress-locked host cannot
install anything casually, and each dependency is another component making network calls and
another supply chain to trust. The trade is some convenience for a much smaller dependency
graph.

**Treat absence as a signal.** The daily digest runs on a schedule, so a missing digest is the
alarm. A host that has stopped working cannot report that itself, so the health check is
something that stops happening.

**Write down the reasoning behind each decision.** The non-obvious ones here — why ingestion is
out-of-band, why the send gate sits outside the agent, why the toolset is restricted — look
arbitrary once the context is forgotten, and are easy to remove without noticing what they
were holding up.