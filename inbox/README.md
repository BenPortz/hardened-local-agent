# inbox/ — read-only email ingestion queue

Each `*.json` here is one **inert** email record written by `scripts/gmail_fetch.py`, a
trusted non-agent process that reads the mailbox read-only. The scheduler triages these with
the agent using a **memory-only toolset** — the agent never has a network or send tool, so it
cannot act on instructions embedded in a message. See
[`docs/email-ingestion.md`](../docs/email-ingestion.md) for why that air-gap is the whole
design rather than an optimization.

**This is not a mailbox mirror, and nothing here sends or deletes anything.** Read-only by
construction: the fetcher makes only API GET calls and refuses to run unless the token's scope
is exactly `gmail.readonly`.

Real records are gitignored. `example-inbox-item.json` shows the shape.

## Record lifecycle

```
  new --(scheduler triage, memory-only toolset)--> triaged --(human-approved action)--> actioned
                                                       |
                                                       +-- triage-failed (step error/timeout)
```

- `new` — fetched, awaiting triage.
- `triaged` — the agent produced `triage` (category / priority / summary / draft reply). Any
  draft reply is **staged text only**; sending is a separate, human-approved step.

## Fields

| field | meaning |
|-------|---------|
| `id` | `gmail-<messageId>` (also the filename) |
| `gmail_id` / `thread_id` | provider message / thread ids |
| `from` / `to` / `subject` / `date` | parsed headers |
| `label_ids` | provider label ids (e.g. `INBOX`, `UNREAD`) |
| `snippet` | the provider's short preview |
| `body` | truncated plaintext (attachments never downloaded); `body_truncated` flags cut-off |
| `state` | `new` / `triaged` / `triage-failed` |
| `triage` | `{category, priority, summary, injection, draft_reply}` — filled by the scheduler |
| `fetched` / `updated` | ISO-8601 UTC timestamps |

## On the `injection` field

The model is asked to flag content that reads as instructions aimed at it. That flag is a
**breadcrumb for the human digest, not a control**. A model that can be talked into complying
can be talked out of flagging, so nothing gates on this value — it is logged and surfaced, and
the actual containment is that the triage step holds no tool capable of acting.
