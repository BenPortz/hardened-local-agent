# launchd service templates (macOS)

Six user LaunchAgents, one per long-lived piece. They are **templates**: substitute the
placeholders before installing, so no machine-specific absolute path is ever committed.

| Placeholder | Meaning | Example |
|---|---|---|
| `__INSTALL_DIR__` | absolute path to this repo on the agent host | `/Users/you/agent` |
| `__HOME__` | the agent user's home directory | `/Users/you` |
| `__FETCH_RUNTIME__` | the **dedicated** Python runtime used only by the mail fetcher | `/Users/you/.agent/fetch-runtime/python` |

Install one:

```bash
sed -e "s|__INSTALL_DIR__|$PWD|g" -e "s|__HOME__|$HOME|g" \
    config/launchd/com.localagent.hub.plist.example \
    > ~/Library/LaunchAgents/com.localagent.hub.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.localagent.hub.plist
```

Check one: `launchctl print gui/$(id -u)/com.localagent.hub | head -20`

## The units

| Unit | Role |
|---|---|
| `model` | local inference server, loopback-only. Everything else depends on it. |
| `hub` | the API + dashboard. Binds the private-mesh interface only. |
| `scheduler` | the orchestrator loop. Idles unless `config/mode.env` says `orchestrator`. |
| `ntfy` | self-hosted push server, private-mesh only. |
| `digest` | the 08:00 daily digest. Its *absence* is the liveness alarm. |
| `mailfetch` | timed read-only mail pull, under its own dedicated interpreter. |

Two constraints worth designing around rather than hiding:

- **Full-disk encryption means user LaunchAgents do not start until someone physically logs
  in after a reboot.** That is a deliberate trade — the disk stays encrypted at rest — and
  the missing daily digest is what tells you the host has not been logged back in.
- **A sleeping host is a dead host.** Configure it to stay awake on AC with the lid closed
  (`pmset -c sleep 0`, `pmset -c disablesleep 1`) before running anything unattended.
