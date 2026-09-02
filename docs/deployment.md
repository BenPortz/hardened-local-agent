# Deployment

Standing up the agent host, in order. Written for macOS on Apple Silicon. The design ports to
Linux with systemd units in place of the launchd templates and an equivalent egress firewall.

The order matters. The model download and the harness installer both need open egress, so the
firewall lock-down comes last. Locking it earlier means loosening it again partway through the
install.

## 0. Host preparation

Use a dedicated machine. Machine isolation is what bounds the blast radius of everything else.

- Wipe it and create a fresh user account.
- Apply pending OS security updates.
- Turn on full-disk encryption. Store the recovery key off this machine, in a password manager
  or on paper, so the host stays self-contained.
- Sign out of the vendor's cloud sync. A synced folder is an egress path that will not appear
  in any firewall rule.

## 1. Model and harness (needs open egress)

Install the inference server and pull a mid-size quantized model, roughly 9-10GB of weights for
a 16GB host. `scripts/bootstrap-macos.sh` covers the scriptable parts.

Check that it is running locally:

```bash
ollama list                              # your model, right size
curl http://127.0.0.1:11434/v1/models    # the endpoint the harness will use
```

Then install the agent harness and point it at the local endpoint. Three details are worth
getting right here, because each one is easy to miss and each one undermines the local-only
property:

- The installer's setup wizard will offer a cloud account login. Skip it, then configure the
  local provider from the CLI: provider `custom`, base URL `http://127.0.0.1:11434/v1`, and
  your local model alias. Confirm no cloud fallback is configured.
- Turn on the harness's approval flags for skill writes and memory writes. They are useful
  interactively, but they do not apply to headless runs. See
  [security-model.md](security-model.md).
- Install any browser-automation dependencies now, before the firewall goes up. They download
  at install time and will fail afterwards.

Confirm the harness is using the local model by starting a conversation and checking that the
local model is loaded while it replies. Note where the harness writes its logs and where its
config lives, since both are needed later.

Run the model server as a managed service using
`config/launchd/com.localagent.model.plist.example`. A GUI launcher that binds the same
inference port will take it at login and the service unit will crash-loop, which is hard to
diagnose from the symptom. Remove the desktop app from login items.

## 2. Repo and configuration

```bash
git clone <this repo> ~/agent && cd ~/agent
cp config/mode.env.example config/mode.env
cp config/cloud_agents.env.example config/cloud_agents.env
mkdir -p ~/.agent && chmod 700 ~/.agent
head -c 32 /dev/urandom | base64 > ~/.agent/hub_token && chmod 600 ~/.agent/hub_token
```

`config/mode.env` starts as `MODE=default`, so nothing runs autonomously yet.

## 3. Private mesh

Install the mesh client and join your personal network. Then confirm the two listening services
bind the mesh interface only:

```bash
lsof -i -P | grep -E 'python|ntfy'    # must show the mesh address, never 0.0.0.0
```

Set `HUB_HOST` and `NTFY_BASE` to the mesh address. The hub still requires its token on every
mutating endpoint; mesh access only makes it reachable.

## 4. Push notifications

Run a self-hosted push server bound to the mesh interface, configured from
`config/ntfy-server.example.yml`, and write its topic to the path `alert.py` reads.

The official release binary for some platforms is client-only and has no `serve` subcommand, so
you may need to build the server from source. If the host's egress is already locked, vendor
the dependencies on another machine and transfer them over the mesh, which avoids opening a
build-time hole.

Subscribe the phone to the topic and add the dashboard to its home screen.

## 5. Egress lock-down

Install a per-application egress firewall and set it to default-deny. Deny anything you are
unsure about; re-allowing later is easy.

The allow-list:

| Allow | Why |
|---|---|
| loopback | the model server |
| the mesh daemon | mesh connectivity |
| the push server binary → its upstream relay, port 443 | instant push wake-ups |
| a dedicated fetcher interpreter → the mail API | out-of-band ingestion |
| cloud-escalation runner binaries → their API hosts | optional escalation |

Deny the model-weights host once the pull is done.

Three things behave differently than you would expect:

1. A default "allow signed OS programs" setting permits the OS network utilities, which can be
   used to move data out. Explicit per-binary block rules override the default. Check for this
   by name.
2. Rules key on the real binary, not the path you invoked. A platform `python3` is often a
   re-exec stub whose real binary is inside a framework directory, and a rule written against
   the stub matches nothing. Find the real path first.
3. Hostname rules do not work under a mesh DNS resolver, because the resolver answers the
   lookup and the firewall never sees the A-record. Scope by process, giving any network-facing
   job its own dedicated binary.

Then run the negative test. The lock-down is unverified without it:

```bash
# from the agent host: an unapproved host must be unreachable
python3 -c "import socket;socket.create_connection(('1.1.1.1',443),timeout=5)"   # must FAIL
curl --max-time 10 https://example.com                                           # must FAIL
ollama run <your-model> "hello"                                                  # must WORK
```

Run this from each interpreter that matters, meaning the plumbing one and the agent's own, and
confirm the agent's interpreter reaches neither external hosts nor the mesh. Re-run it after
any firewall change, and after each reboot if your firewall's rules are runtime state rather
than persisted policy.

## 6. Keep-awake

A host that goes to sleep stops working and cannot report that it has.

```bash
sudo pmset -c sleep 0            # never sleep on AC
sudo pmset -c disablesleep 1     # keep running with the lid closed (AC only)
pmset -g custom                  # verify
```

Revert `disablesleep` before travelling on battery.

## 7. Services

Install the launchd units from `config/launchd/`, using the README there for placeholder
substitution. Bring them up in dependency order: model, push, hub, scheduler, digest, and the
mail fetcher last.

Full-disk encryption means user agents do not start until someone logs in physically after a
reboot. That is the expected trade, and a missing daily digest is what tells you it has
happened.

## 8. Rollout discipline

The reasoning is in [security-model.md](security-model.md):

1. Stand up with throwaway or read-only credentials.
2. Run everything dry-run or draft-only, and read the Tier C digests and the raw audit log.
3. Negative-test the egress lock, as in step 5.
4. Run an injection smoke test: feed the agent a local fixture message with an embedded
   "forward X to attacker@…" instruction, using the same invocation mode and toolset the
   scheduler uses. The gate should hold and the event should appear in the logs. A gate that
   holds interactively says nothing about a headless run.
5. Only then issue real least-privilege credentials, starting narrow.

## Operating notes

- Switching to `MODE=default` in `config/mode.env` stops autonomous work after the current
  step. This is the stop control.
- Watch for the morning digest. Its absence is what indicates a problem.
- Re-verify egress after each reboot if the rules are runtime state.
- Review the firewall rule list periodically, particularly after installs. Temporary allow
  rules added for a one-off install tend to stay.
