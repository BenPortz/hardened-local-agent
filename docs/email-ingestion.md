# Email ingestion — out-of-band, read-only

How the agent gets access to a real mailbox for triage without a socially-engineered agent
ever being able to send, delete, or leak.

**Principle: prove the cage before putting anything real in it.**

## The architectural decision

The obvious design is to give the agent a mail tool — an MCP server or a skill holding the
credential. **This design deliberately does not do that.**

Because the agent is assumed to be injectable by the mail it reads, a mail tool in the agent's
hands is a tool an attacker can reach by sending an email. So ingestion is inverted:

```
  trusted NON-agent fetcher            agent (air-gapped from the mailbox)
  ------------------------             ---------------------------------
  holds the read-only credential
  makes the only network calls
  writes inert JSON records  ------->  reads inbox/*.json with a memory-only toolset
                                       no network tool, no send tool, no credential
                                       writes triage + draft text back into the record
                                            |
                                            v
                                       human reviews on the dashboard
                                            |
                                            v
                                       separate trusted sender (not built here)
```

The agent never holds the credential, never makes a network call, and holds no tool capable of
acting on an instruction embedded in a message. An injection that fully succeeds at the
rhetorical level still has nothing to execute with.

## Credential design

- **Gmail API with a read-only OAuth scope (`gmail.readonly`)**, chosen over IMAP with an app
  password for one decisive reason: it supports a genuine read-only scope. IMAP app passwords
  grant full read **and** send, with no read-only option — a credential that cannot express
  the constraint you need is the wrong credential.
- **A dedicated OAuth client**, separate from any other client on the account, so the agent's
  access is independently revocable in one click without disturbing anything else.
- **The consent screen stays in testing mode** with the owner as the only test user. There is
  no third party to publish for, and it avoids restricted-scope verification entirely.
- **Tokens are minted interactively and never handled by an assistant.** `gmail_auth.py`
  performs a stdlib loopback + PKCE flow, and **refuses to save a token carrying any scope
  broader than read-only** — defense in depth, in case the consent screen ever hands back
  more than was asked for.
- **Credentials live outside the repo and outside the agent's reach**, directory `700`, files
  `600`.
- `gmail_fetch.py` **re-asserts the read-only scope at startup** and exits if the token
  carries anything else. Two independent checks, because the cost of being wrong is a
  mutating credential in an injectable system.

## The fetcher

`scripts/gmail_fetch.py` — a trusted, non-agent process.

Invariants, each enforced in code rather than assumed from the OAuth scope:

- **Read-only** — HTTP GET only; refuses to run unless the token's scope is exactly
  `gmail.readonly`.
- **Least data** — headers plus a truncated plaintext body. Attachments are never downloaded.
- **Audited** — every run logs one `email.read` event to Tier A with host and credential id.
  This is what closes the audit gap for the ingestion path: the fetcher logs its own actions
  because the harness will not log them for it.
- **Idempotent** — a message already present in `inbox/` is skipped, so a doubled or missed
  scheduled run is harmless.
- **Stdlib only** — the Gmail API is plain REST/JSON, so token refresh, list and get are a
  handful of `urllib` calls. This avoids the official client library entirely, which matters
  on a host where pip cannot reach the network anyway, and keeps both the dependency surface
  and the egress surface minimal.
- **Testable without credentials** — `--self-test` exercises MIME parsing and the record
  writer against a synthetic payload. No token, no network, runs in CI or on a laptop.

### The egress-scoping problem, and the fix

The fetcher needs to reach the mail API — external hosts that a default-deny egress policy
correctly blocks. Opening that hole without opening it for everything else turned out to be
the subtlest part of the build:

- **A hostname-based firewall rule does not work under a mesh DNS resolver.** The mesh
  resolver answers the lookup, so the firewall never observes the A-record it would need to
  map the API's rotating IPs back to a hostname. The rule matches nothing.
- **A broad allow on the system Python re-opens external egress for every script that shares
  that interpreter** — including the plumbing that is supposed to stay caged. Unacceptable.

**The fix is process isolation: give the fetcher its own dedicated Python interpreter**, used
for nothing else. Application firewalls key their rules on the real binary, so a dedicated
interpreter is a distinct identity that can carry a network allow while every other
interpreter on the host stays locked to loopback. Even a broad allow on that binary has a
blast radius of exactly one program: the read-only fetcher.

This is the same "one runner binary per egress hole" pattern the cloud-escalation path uses.
It generalizes: **when you cannot scope a rule by destination, scope it by process.**

Two traps worth knowing before you write firewall rules:

- **Rules key on the real binary, not the path you invoked.** A platform's `python3` may be a
  re-exec stub pointing at a framework binary elsewhere; a rule written against the stub
  matches nothing at all, silently.
- **A default "allow signed OS programs" setting silently allows the OS's own network
  utilities**, which are a ready-made exfiltration path. Explicit per-binary block rules
  override the default. Test for this specifically rather than assuming.

## Triage

The scheduler feeds `inbox/` records to the agent with a **memory-only toolset**: no network,
no send, no file tools. The agent classifies, summarizes, and may stage draft reply text back
into the record. That draft is inert text on disk.

The triage prompt marks the message body explicitly as **data, never instructions**, and asks
the model to flag anything that looks like an instruction aimed at it.

**That flag is a breadcrumb for the human digest, not a control.** A model that can be talked
into complying can equally be talked out of flagging, and it will sometimes do both in the
same run. It is logged; nothing gates on it. The containment is the missing tool, not the
model's judgment.

## Record lifecycle

```
  new --(scheduler triage, memory-only toolset)--> triaged --(human-approved action)--> actioned
                                                       |
                                                       +-- triage-failed (step error/timeout)
```

Schema and field reference: [`inbox/README.md`](../inbox/README.md).

## The send gate (design; not implemented here)

Sending is the first write capability in the whole system, and it gets built last and
separately:

1. The agent drafts. It has no send tool and no credential, so this is the *only* thing it can
   do.
2. The draft surfaces on the dashboard for human review — a push notification, subject and
   body, no send button in the agent's path.
3. An explicit human approval releases exactly that one draft.
4. A **separate trusted process**, holding a **separate narrow send credential** the agent
   cannot read, performs the send and logs it as a Tier B event.

The gate is structural at every step. There is no prompt to bypass, because there is no
prompt: the agent is not in the send path at all.

## Operational notes

- **Start narrow.** First fetch with a tight query and a small `--max` to validate the
  pipeline before pulling in bulk.
- **`--max` caps the listing, not the number of new records.** A large cold-start backlog
  therefore does not drain on its own — the oldest messages simply never appear. Widen the
  query once to catch up, then return to the steady-state schedule.
- **Re-run the egress negative test after opening the mail allow**, and confirm two things:
  only the mail API opened, and the agent's own interpreter is still loopback-only.
