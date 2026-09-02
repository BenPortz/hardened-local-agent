# Monitoring and audit — the A/B/C/D tiers

Cadence is matched to risk. High-risk events notify immediately, routine events are reviewable
in context afterwards, and patterns are aggregated over time. What keeps the output readable is
surfacing whatever is new or unknown.

| Tier | What | Cadence | Implementation |
|------|------|---------|----------------|
| **A — Silent log** | Every agent action, append-only | Always, no notification | `scripts/audit_logger.py` — structured JSONL; the source of truth |
| **B — Real-time alert** | High-risk events only | As they happen | `scripts/alert.py` — push and desktop notification |
| **C — Per-workflow digest** | Summary of what one routine did | End of each workflow | `scripts/workflow_digest.py` — the main review surface |
| **D — Nightly roll-up** | Trends and anomalies across the day | Nightly | `scripts/nightly_rollup.py` — aggregates Tier A |
| **D+ — Daily digest push** | Service health, project states, 24h roll-up, pushed to the phone | 08:00 daily | `scripts/daily_digest.py` — a missing digest indicates a problem; any service down raises the priority |

## What Tier B fires on

Five event classes:

1. An outbound send (email, chat, and so on)
2. A delete or a settings change
3. A credential access
4. A connection to an external host that is not on the allowlist
5. A newly created skill about to run for the first time

Everything else is logged at Tier A and summarized at Tier C without interrupting anyone.

## Tier A — audit-log schema

One JSON object per line:

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

`new_vs_known` is computed against the allowlists in `allowlists/`. Anything marked `NEW` is a
candidate for a Tier B alert.

## Tier C — per-workflow digest

Example output:

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

## Tier D — nightly roll-up

- skill-creation rate, and which skills
- new external hosts seen, count and list
- credential-access frequency compared to baseline
- total outbound actions compared to baseline

## Design notes

**Detection is deterministic.** Tiers A and D are diffs, allowlist lookups and log parsing. A
model may reformat a digest for readability downstream. It never decides whether anything is
safe: it is unreliable at self-judgment, and it is injectable by the content it would be
reviewing.

**Alerting is limited to the five classes above.** A monitoring surface that notifies too often
stops being read, which leaves the appearance of oversight without the substance.

**Logging never blocks on alerting.** Alerting is best-effort and its failures are swallowed by
the caller, so a push server being down cannot stop an event from being recorded. The log is
the source of truth and the alert sits on top of it.
