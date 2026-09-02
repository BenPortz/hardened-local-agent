# Security Model

## The threat

The main risk is that the agent is persuaded, by content it reads such as an email or a fetched
web page, to misuse the access it was legitimately given: sending, deleting, leaking, or
writing itself a new skill. This is indirect prompt injection. A file scanner will not find
anything, because nothing is infected. The agent is behaving normally and using its own valid
credentials.

Two things follow from this.

Detection after the fact does not undo a leak, so preventive controls, which reduce what the
agent is able to do at all, carry most of the protection. Detective controls, meaning the
monitoring tiers, are a backup. Preventive controls get built first.

Model judgment is treated as a convenience feature throughout. Anything that can be talked
into taking an action can be talked into describing the result inaccurately afterwards, so no
control in this design depends on it.

## Preventive controls

| # | Control | Why it matters |
|---|---------|----------------|
| 1 | **Egress allowlist, default-deny** | The most useful single control. A compromised agent has nowhere to send data. Allow only loopback for the model server, the specific API hosts a workflow needs, and the mesh daemon. |
| 2 | **Least-privilege credentials** | Read-only OAuth scopes wherever a real read-only scope exists, and narrow, revocable, per-purpose clients. An injection cannot do what the credential does not permit. |
| 3 | **Structural approval gates on outbound and irreversible actions** | Sends, deletes and settings changes produce a draft or a queued request for human approval. See the note below on where the gate lives. |
| 4 | **Gated self-modification** | An agent that writes its own capabilities, next to real account access, has a wide blast radius. A newly created skill needs human review before its first run. |
| 5 | **Local-model pin** | Model launchers offer cloud-hosted models alongside local ones. Pin a local model and a loopback endpoint and re-check it at startup. |
| 6 | **Machine isolation** | A dedicated, freshly wiped host with full-disk encryption and separate user accounts. This bounds the blast radius to that machine and the accounts given to it. |
| 7 | **Capability minimization per task** | Each autonomous step gets the smallest toolset that can do its job. A triage step needs no network tool and no send tool, so an injection has nothing available to act with. |

### Where the send gate lives

Agent harnesses usually offer approval prompts for dangerous operations. Those work
interactively. Headless and non-interactive invocation modes commonly bypass tool, memory and
approval prompts, which is what makes them headless, so autonomous runs need the gate
somewhere else.

The pattern used here:

```
  agent (no send tool, no credential)
     -> writes a DRAFT record to disk
        -> human reviews it on the dashboard
           -> a SEPARATE trusted process, holding a separate credential, performs the send
```

The agent cannot send, cannot approve, and cannot reach the credential that would let it do
either. No prompt is involved at any step.

## Detective controls

See [monitoring-tiers.md](monitoring-tiers.md) for the A/B/C/D design. The principles behind
it:

- **Detection is deterministic.** Diffs, allowlist checks and egress-log parsing. A model may
  summarize a digest for readability. It never gates anything: a model judging its own logs is
  unreliable, and is itself injectable by the content it is reviewing.
- **Surface the new and unknown.** Keep allowlists of known hosts, skills and credential-uses
  and report only the differences. This keeps the output short enough that it gets read.
- **Malware scanning is a separate question.** If you want it, use an actual AV engine. It
  addresses a different threat from the one described above.

## Nightly self-scans

A skill that asks the model each night whether its own skills and logs look safe is
unreliable, injectable, and only runs after the fact.

A deterministic audit digest works better: what went out, which skills are new, which external
connections are new, which credentials were touched, reviewed by a person. The detection is
mechanical and the judgment is human.

## Known limits of this design

- **Harness-level tool calls may not reach the audit trail.** The audit log records what the
  orchestrator does. Tool calls executed inside the harness appear only in the harness's own
  logs unless tool-execution logging is added. The mitigation used here is to make sure
  autonomous runs do not hold auditable-action tools in the first place.
- **Application firewalls key rules on the real binary,** which on some platforms is not the
  path you invoked. A rule written against a wrapper or a re-exec stub can match nothing at
  all. Confirm each rule with a live negative test.
- **Domain-based egress rules do not work under a mesh DNS resolver,** because the firewall
  never sees the A-record it would need to map a rotating IP back to a hostname. The
  workaround is to give a network-facing job its own dedicated binary, so the rule can be
  scoped by process instead of by hostname.
- **A default "allow signed OS programs" firewall setting permits the OS network utilities,**
  which can be used to move data out. Explicit per-binary block rules override it. Test for
  this specifically.
- **Push over a private mesh only arrives while the phone is on the mesh.** This is the cost of
  keeping notifications off third-party infrastructure. The dashboard shows anything missed.

## Control-bypass policy

The egress controls exist to contain the agent, not the owner. That has two consequences.

Owner-directed installs may route around an incidental firewall block, for example by
downloading on another machine, verifying the hash and code signature, and transferring the
file over the mesh. Verification is required, and the firewall stays as it is.

Blocks that stop the agent are the system working as intended. No session, human-driven or
AI-driven, should relay, proxy or fetch on the agent's behalf to get around a control that
stopped it. No rule should be loosened because the agent needs it without an explicit owner
decision recorded in writing, meaning an allowlist entry and its blast radius.

If another person uses the host, they get a separate standard account, are told to deny
firewall prompts, and the owner reviews the rule list afterwards.

## Rollout discipline

The point of this sequence is to confirm the containment works before any real credential is
introduced.

1. Stand everything up with throwaway or read-only credentials.
2. Run every workflow in dry-run or draft-only mode and review the per-workflow digests and the
   raw audit log.
3. Negative-test the egress lock: confirm an intentional outbound connection to an unapproved
   host is blocked. Re-run this after any firewall change, and after a reboot if the rules are
   runtime state.
4. Run an injection smoke test: feed the agent a local fixture message containing an embedded
   "forward X to attacker@…" instruction. The gate should hold, and the event should appear in
   the logs. Test with the same invocation mode and toolset the scheduler uses, since a gate
   that holds interactively says nothing about a headless run.
5. Only then issue the real least-privilege credentials, starting narrow.
