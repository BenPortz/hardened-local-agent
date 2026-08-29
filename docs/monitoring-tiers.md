# Monitoring & Audit — The A/B/C/D Tiers

Cadence matches risk: dangerous events page you instantly; routine events are reviewable in
context; patterns surface over time. **Signal-to-noise rule: surface the *new/unknown*, not
the known.**

| Tier | What | Cadence | Implementation |
|------|------|---------|----------------|
| **A — Silent log** | *Every* agent action, append-only, immutable | Always (no notification) | `scripts/audit_logger.py` — structured JSONL; forensic source of truth |
| **B — Real-time alert** | High-risk events only | The moment they happen | `scripts/alert.py` — macOS notification/push; optional approval prompt |
| **C — Per-workflow digest** | Summary of what one routine did | End of each routine/workflow | `scripts/workflow_digest.py` — terse, contextual; **primary** review surface |
| **D — Nightly roll-up** | Trends & anomalies across the day | Nightly | `scripts/nightly_rollup.py` — aggregates Tier A; pattern-spotting only |
| **D+ — Daily digest push** | Service health + project states + 24h roll-up, pushed to the phone | 08:00 daily (scheduled service) | `scripts/daily_digest.py` — a *missing* digest is the liveness alarm; any service DOWN escalates priority |

## Tier B fires ONLY for these high-blast-radius event classes

1. An **outbound send** (email / Slack / etc.)
2. A **delete or settings change**
3. A **credential access** event
4. A connection to a **brand-new external host** (not on the allowlist)
5. A **newly-created skill about to run for the first time**

Everything else is logged silently at Tier A and summarized at Tier C — no interruption.

## Tier A — audit-log schema (one JSON object per line)

```json
{
  "timestamp": "2026-01-15T07:00:13Z",
  "workflow_id": "morning-email",
  "action_type": "email.read | email.draft | email.send | skill.create | skill.run | net.connect | cred.access | settings.change | ...",
  "target": "<recipient / file / setting / skill name>",
  "external_host": "gmail.googleapis.com | null",
  "credential_touched": "gmail_readonly | null",
  "payload_summary": "<short, non-sensitive description>",
  "new_vs_known": "known | NEW",
  "result": "ok | blocked | awaiting_approval | error",
  "tier_b_triggered": false
}
```

The `new_vs_known` flag is computed against the allowlists in `allowlists/`. Anything `NEW`
is a candidate for a Tier B alert.

## Tier C — per-workflow digest (example)

```
[morning-email · 07:00–07:02]  6 actions
  reads: 12 emails (gmail, known)
  drafts: 2 replies  → AWAITING APPROVAL
  skills used: summarize_thread (known), label_email (known)
  new skills: none
  external hosts: gmail.googleapis.com (known)
  credentials: gmail_readonly (known)
  ⚠ Tier B events: 0
```

## Tier D — nightly roll-up highlights

- skill-creation rate (and which)
- any new external hosts seen (count + list)
- unusual credential-access frequency vs. baseline
- total outbound actions vs. baseline

## Honest note

Detection in Tiers A and D is **deterministic** — diffs, allowlist checks, egress-log parsing.
An LLM may *summarize* a digest for readability but must **never** be the safety gatekeeper
(it is unreliable at self-judgment and is itself injectable by the very content it reviews).

## What this design deliberately does not do

- **No LLM anywhere in the detection path.** Tiers A and D are diffs, allowlist lookups and
  log parsing. A model may render a digest into friendlier prose downstream; it never decides
  whether something is safe.
- **No alerting on the routine.** Tier B fires on five event classes and nothing else. A
  monitoring surface that cries wolf is a monitoring surface nobody reads, which is strictly
  worse than none — it manufactures the feeling of oversight without the fact of it.
- **No blocking in the log path.** Alerting is best-effort and every failure is swallowed by
  the caller: a push server being down must never stop an event from being recorded. The log
  is the source of truth; the alert is a convenience on top of it.
