# Deployment

Standing up the agent host, in order. Written for macOS on Apple Silicon; the design ports to
Linux with systemd units in place of the launchd templates and an equivalent egress firewall.

**Order matters.** The model download and the harness installer both need open egress, so the
firewall lock-down comes **last**. Do not reorder this to "be safe earlier" — you will end up
loosening the firewall to finish the install, which is strictly worse than locking it once at
the end.

## 0. Host preparation

A **dedicated** machine. Not your daily driver — machine isolation is control #6, and it is
the one that bounds the blast radius of everything else.

- Wipe it and create a fresh user account.
- Apply pending OS security updates.
- **Turn on full-disk encryption.** Store the recovery key **off this machine** — a password
  manager or paper. Not the vendor's cloud, if you want the host genuinely self-contained.
- Sign out of the vendor's cloud sync entirely. A synced folder is an egress path that no
  firewall rule will show you.

## 1. Model and harness (needs open egress — do this first)

Install the inference server and pull a mid-size quantized model — roughly 9-10GB of weights
for a 16GB host. `scripts/bootstrap-macos.sh` covers the scriptable parts.

Verify it is genuinely local:

```bash
ollama list                              # your model, right size
curl http://127.0.0.1:11434/v1/models    # the endpoint the harness will use
```

Then install the agent harness and point it at the **local** endpoint.

Three things to get right, each of which silently breaks the privacy guarantee if you do not:

- **The installer's setup wizard will push a cloud account login.** Skip it. Configure the
  local provider afterwards from the CLI: provider `custom`, base URL
  `http://127.0.0.1:11434/v1`, and your local model alias. Confirm no cloud fallback is
  configured anywhere.
- **Turn on the harness's own approval flags** for skill writes and memory writes. Useful
  interactively. Remember they do *not* bind headless runs — see
  [security-model.md](security-model.md).
- **Install any browser-automation dependencies now, before the firewall goes up.** They
  download at install time and will fail confusingly afterwards.

Verify the harness is really using the local brain: start a conversation and confirm the local
model shows as loaded while it replies. Note where the harness writes its logs and where its
config lives — you need both later.

Run the model server as a **managed service** (`config/launchd/com.localagent.model.plist.example`),
not as a desktop app. A GUI launcher that also binds the inference port will fight your service
unit for it and win at login, and the symptom — a crash-looping unit — does not obviously point
at its cause. Remove the desktop app from login items.

## 2. Repo and configuration

```bash
git clone <this repo> ~/agent && cd ~/agent
cp config/mode.env.example config/mode.env
cp config/cloud_agents.env.example config/cloud_agents.env
mkdir -p ~/.agent && chmod 700 ~/.agent
head -c 32 /dev/urandom | base64 > ~/.agent/hub_token && chmod 600 ~/.agent/hub_token
```

`config/mode.env` starts as `MODE=default` — nothing runs autonomously until you say so.

## 3. Private mesh

Install the mesh client and join your personal network. Then confirm the two listening
services bind the **mesh interface only**:

```bash
lsof -i -P | grep -E 'python|ntfy'    # must show the mesh address, never 0.0.0.0
```

Set `HUB_HOST` and `NTFY_BASE` to the mesh address. Being on the mesh is **not**
authentication — the hub still requires its token on every mutating endpoint.

## 4. Push notifications

Run a self-hosted push server bound to the mesh interface, configured from
`config/ntfy-server.example.yml`, and write its topic to the path `alert.py` reads.

Note that the official release binary for some platforms is **client-only** and has no `serve`
subcommand; you may need to build the server from source. If the host's egress is already
locked, vendor the dependencies on another machine and transfer them over the mesh rather than
opening a build-time hole.

Subscribe the phone to the topic, and add the dashboard to its home screen.

## 5. Egress lock-down (LAST)

Install a per-application egress firewall and put it in **default-deny**. Deny everything by
default; when in doubt, deny — you can always re-allow.

The allow-list, and nothing else:

| Allow | Why |
|---|---|
| loopback | the model server |
| the mesh daemon | mesh connectivity |
| the push server binary → its upstream relay, port 443 | instant push wake-ups |
| a **dedicated** fetcher interpreter → the mail API | out-of-band ingestion |
| cloud-escalation runner binaries → their API hosts | optional escalation |

Deny the model-weights host once the pull is done, and re-lock.

Three traps that will cost you hours if you meet them cold:

1. **A default "allow signed OS programs" setting silently permits the OS's own network
   utilities** — a ready-made exfiltration path that renders the whole lock-down decorative.
   Explicit per-binary **block** rules override the default. Test for this by name.
2. **Rules key on the real binary, not the path you invoked.** A platform `python3` is often a
   re-exec stub whose real binary lives inside a framework directory; a rule written against
   the stub matches nothing, silently. Find the real path before writing the rule.
3. **Hostname rules do not work under a mesh DNS resolver.** The resolver answers the lookup,
   so the firewall never sees the A-record and cannot map a rotating IP to a hostname. Scope
   by *process* instead — give any network-facing job its own dedicated binary.

**Negative test — required, and not optional:**

```bash
# from the agent host: an unapproved host must be unreachable
python3 -c "import socket;socket.create_connection(('1.1.1.1',443),timeout=5)"   # must FAIL
curl --max-time 10 https://example.com                                           # must FAIL
ollama run <your-model> "hello"                                                  # must WORK
```

Run this from **each** interpreter that matters — the plumbing one and the agent's own — and
confirm the agent's interpreter reaches neither external hosts nor the mesh. Re-run it after
**any** firewall change, and after every reboot if your firewall's rules are runtime state
rather than persisted policy.

## 6. Keep-awake

A sleeping host is a dead host, and a dead host cannot tell you it is dead.

```bash
sudo pmset -c sleep 0            # never sleep on AC
sudo pmset -c disablesleep 1     # keep running with the lid closed (AC only)
pmset -g custom                  # verify
```

Revert `disablesleep` before travelling on battery.

## 7. Services

Install the launchd units from `config/launchd/` — see the README there for placeholder
substitution. Bring them up in dependency order: model, push, hub, scheduler, digest, and the
mail fetcher last.

Note that full-disk encryption means user agents do **not** start until someone physically
logs in after a reboot. That is a deliberate trade, and the missing daily digest is what tells
you it has happened.

## 8. Rollout discipline

Do not skip to the end. Full rationale in [security-model.md](security-model.md):

1. Stand up with **throwaway or read-only credentials**.
2. Run everything **dry-run / draft-only**; read the Tier C digests and the raw audit log.
3. **Negative-test the egress lock** (step 5).
4. **Injection smoke test** — feed a local fixture message with an embedded "forward X to
   attacker@…" instruction, using the *exact* invocation mode and toolset the scheduler uses.
   The gate must hold and the event must appear in the logs. A gate that holds interactively
   proves nothing about a headless run.
5. Only then issue real least-privilege credentials, and start narrow.

## Operating notes

- **Switch modes with one line.** `MODE=default` in `config/mode.env` stops autonomous work at
  the end of the current step. It is the kill switch.
- **Watch for the morning digest.** Its absence is the alarm.
- **Re-verify egress after every reboot** if rules are runtime state.
- **Review the firewall rule list periodically**, especially after installs — temporary allow
  rules added for a one-off install have a way of becoming permanent.
