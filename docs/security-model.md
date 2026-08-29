# Security Model

## The real threat

The danger is **not** classic malware appearing on disk. It is that the **trusted agent gets
socially engineered — through content it reads (an email, a fetched web page) — into misusing
the *legitimate* access you gave it**: sending, deleting, leaking, or writing itself a
malicious skill. This is **indirect prompt injection**. A file scanner looks for the wrong
thing entirely; nothing "infected" ever shows up, because the agent simply acts with its own
valid credentials.

Two corollaries drive everything else:

- **Detection after the fact cannot un-leak data.** So preventive controls — which shrink what
  the agent *can* do — carry roughly 80% of the protection, and detective controls
  (monitoring) carry roughly 20%. Build preventive first.
- **The model is not a security boundary.** Any model that can be talked into taking an action
  can be talked into describing the outcome inaccurately afterwards. Treat model judgment as a
  convenience feature and never as a control.

## Preventive controls (the heavy lifting)

| # | Control | Why it matters |
|---|---------|----------------|
| 1 | **Egress allowlist, default-deny** | Highest value by a wide margin. Even a fully hijacked agent has nowhere to exfiltrate to. Allow only: loopback (the model server), the specific API hosts a workflow needs, and the mesh daemon. |
| 2 | **Least-privilege credentials** | Read-only OAuth scopes wherever a real read-only scope exists; narrow, revocable, per-purpose clients. Injection cannot do what the keys do not permit. |
| 3 | **Structural approval gates on outbound and irreversible actions** | Sends, deletes and settings changes produce a *draft or a queued request*, never an auto-send. The single highest-value behavioral control — see the note below on why it must be structural. |
| 4 | **Gate self-modification** | A self-improving agent that writes its own capabilities, next to real account access, is a large blast radius. Require human review before a newly created skill runs for the first time. |
| 5 | **Local-model pin** | Model launchers happily offer cloud-hosted models. Pin a local model and a loopback endpoint, and re-verify it at startup; otherwise prompts silently leave the machine. |
| 6 | **Machine isolation** | A dedicated, freshly wiped host with full-disk encryption and separate user accounts. Bounds the blast radius to this box and the specific accounts handed to it. |
| 7 | **Capability minimization per task** | Give each autonomous step the smallest toolset that can do its job. A triage step needs no network tool and no send tool, so an injection that lands has nothing to act with. |

### Why the send gate must be structural

Agent harnesses typically offer approval prompts for dangerous operations. Those are useful
interactively and **must not be relied on for autonomous runs**: headless and non-interactive
invocation modes commonly bypass tool, memory and approval prompts by design — that is what
makes them headless.

So the gate cannot live inside the agent's own execution path. The pattern used here:

```
  agent (no send tool, no credential)
     -> writes a DRAFT record to disk
        -> human reviews it on the dashboard
           -> a SEPARATE trusted process, holding a separate credential, performs the send
```

The agent cannot send, cannot approve, and cannot reach the credential that would let it. No
prompt is involved, so no prompt can be bypassed.

## Detective controls (backup layer)

See [monitoring-tiers.md](monitoring-tiers.md) for the A/B/C/D design. Key principles:

- **Detection is deterministic** — diffs, allowlist checks, egress-log parsing. An LLM judging
  its own logs for "safety" is unreliable *and itself injectable by the very content it is
  reviewing*. It may **summarize** a digest for readability; it may never be the gatekeeper.
- **Surface the new and unknown, not the known.** Maintain allowlists of known hosts, skills
  and credential-uses, and report deltas only. This is what keeps monitoring glanceable enough
  that a human actually reads it.
- Real malware scanning, if wanted, means an actual AV engine — not an LLM. But it addresses a
  threat that is not the main risk here.

## Why nightly self-scans are security theater

- A skill that asks the model "are my skills and logs safe?" every night is unreliable,
  injectable, and after the fact.
- A **deterministic audit digest** — what went out, which skills are new, which external
  connections are new, which credentials were touched — reviewed by a **human**, is genuinely
  useful. Detection is mechanical; the human is the judge.

## Known limits of this design

Stated plainly, because a security model that only lists its strengths is marketing:

- **Harness-level tool calls are not necessarily in the audit trail.** The audit log records
  what the orchestrator does. If the harness executes tool calls internally, those are visible
  only in the harness's own logs unless tool-execution logging is added. The mitigation used
  here is to ensure autonomous runs never hold auditable-action tools in the first place.
- **Application firewalls key rules on the real binary**, which on some platforms is not the
  path you invoked. A rule written against a wrapper or re-exec stub can silently match
  nothing. Verify every rule with a live negative test, not by reading the rule list.
- **Domain-based egress rules break under a mesh DNS resolver**, because the firewall never
  sees the A-record it would need to map a rotating IP back to a hostname. The workaround is
  to give a network-facing job its own dedicated binary so an allow-rule can be scoped by
  process instead of by hostname.
- **A default "allow signed OS programs" firewall setting silently allows the OS's own network
  utilities**, which are a ready-made exfiltration path. Explicit per-binary block rules
  override it; test for this specifically.
- **Push over a private mesh only arrives while the phone is on the mesh.** That is the
  accepted cost of notifications never touching a third-party cloud; the dashboard shows
  anything missed.

## Control-bypass policy

The egress controls exist to contain the **agent**, not the owner. Two consequences:

- **Owner-directed installs may deliberately route around an incidental firewall block** — for
  example, download on another machine, verify hash and code signature, then transfer over the
  mesh. Verification is mandatory. Loosening the firewall to avoid the detour is not an option.
- **Agent-facing blocks are the system working.** No session, human-driven or AI-driven, may
  relay, proxy or fetch on the agent's behalf to get around a control that stopped the agent,
  and no rule may be loosened *because the agent needs it* without an explicit owner decision
  recorded in writing — an allowlist entry plus its blast radius.
- **Guest use of the host** (a separate standard account for another person) means: guests are
  briefed to deny firewall prompts, and the owner reviews the rule list after guest sessions.

## Rollout discipline

Prove the cage before putting anything real in it.

1. Stand everything up with **throwaway or read-only credentials**.
2. Run every workflow in **dry-run / draft-only** mode; review the per-workflow digests and the
   raw audit log.
3. **Negative test the egress lock:** confirm an intentional outbound connection to an
   unapproved host is blocked. Re-run this after *any* firewall change, and after any reboot
   if rules are runtime state.
4. **Injection smoke test:** feed the agent a local fixture message containing an embedded
   "forward X to attacker@…" instruction. The gate must hold — no send — and the event must be
   visible in the logs. Test with the *exact* invocation mode and toolset the scheduler uses;
   a gate that holds interactively proves nothing about a headless run.
5. Only then issue the real least-privilege credentials, and start narrow.
