# inbox/ — read-only email ingestion queue

Each `*.json` here is one inert email record written by `scripts/gmail_fetch.py`, a trusted
non-agent process that reads the mailbox read-only. The scheduler triages these with the agent
using a memory-only toolset, so the agent has no network or send tool and cannot act on
instructions embedded in a message. See
[`docs/email-ingestion.md`](../docs/email-ingestion.md) for the reasoning behind that split.

Nothing here sends or deletes. The fetcher makes only API GET calls and refuses to run unless
the token's scope is exactly `gmail.readonly`.

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

The model is asked to flag content that reads as instructions aimed at it. That flag is a note
for the human digest. Nothing gates on this value, since a model that can be talked into
complying can also be talked out of flagging. It is logged and surfaced, and the containment
comes from the triage step holding no tool capable of acting.
