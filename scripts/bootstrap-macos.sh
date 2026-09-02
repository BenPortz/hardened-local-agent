#!/bin/bash
# Agent host bootstrap (scriptable parts only). macOS / Apple Silicon.
# Companion to docs/deployment.md. GUI steps are NOT covered here: full-disk encryption,
# firewall configuration, and mesh login all need a human at the machine.
#
# ORDER MATTERS: the model pull and the harness installer both need OPEN egress, so this
# script runs BEFORE the firewall lock-down. Do not reorder.
set -euo pipefail

MODEL_REPO="${MODEL_REPO:-<publisher>/<Model-GGUF>:Q4_K_M}"   # a mid-size 4-bit quant, ~9-10GB
MODEL_ALIAS="${MODEL_ALIAS:-localagent:14b}"
HARNESS_INSTALLER="${HARNESS_INSTALLER:-https://hermes-agent.nousresearch.com/install.sh}"

echo "== [1/4] Pull the local brain (~9GB, needs open egress, run BEFORE the firewall) =="
echo "    model: $MODEL_REPO  ->  alias: $MODEL_ALIAS"
ollama pull "$MODEL_REPO"
ollama cp "$MODEL_REPO" "$MODEL_ALIAS"
echo "-- smoke test (the first reply is slow while the model loads) --"
ollama run "$MODEL_ALIAS" "Reply with exactly: local brain online"

echo
echo "== [2/4] Download the agent harness installer (NOT run automatically) =="
curl -fsSL "$HARNESS_INSTALLER" -o "$HOME/install-harness.sh"
echo "Saved to ~/install-harness.sh"
echo "Review it before running. Check which hosts it fetches from, whether it edits your"
echo "shell profile, and any sudo use:   less ~/install-harness.sh"
read -r -p "Reviewed and ready to run the installer now? [y/N] " yn
if [[ "${yn:-n}" == "y" || "${yn:-n}" == "Y" ]]; then
  bash "$HOME/install-harness.sh"
  echo
  echo "After install, configure the LOCAL provider (skip any cloud-account wizard):"
  echo "  provider = custom"
  echo "  base_url = http://127.0.0.1:11434/v1"
  echo "  model    = $MODEL_ALIAS"
  echo "  enable the skill-write and memory-write approval flags"
  echo "Install browser-automation deps NOW if you want them; they need egress."
else
  echo "Skipped. Run later with: bash ~/install-harness.sh"
fi

echo
# Example stack. Substitute any per-application egress firewall and any WireGuard-based
# mesh; the design depends on the capabilities, not on these particular products.
FIREWALL_CASK="${FIREWALL_CASK:-lulu}"
MESH_CASK="${MESH_CASK:-tailscale}"
echo "== [3/4] Install the egress firewall + mesh client (configuration is manual) =="
brew install --cask "$FIREWALL_CASK" "$MESH_CASK"

echo
echo "== [4/4] Keep-awake on AC power (asks for your password) =="
sudo pmset -c sleep 0
sudo pmset -c disablesleep 1
pmset -g custom

cat <<'DONE'

================================================================
Scriptable parts done. Remaining MANUAL steps, in this order:

  1. Full-disk encryption ON (System Settings > Privacy & Security).
     Store the recovery key OFF this machine.
  2. Mesh client: log in to your personal network. Note the host's
     mesh address; set HUB_HOST / NTFY_BASE to it.
  3. Firewall: default-deny, passive mode OFF. Allow ONLY loopback,
     the mesh daemon, the push binary -> its relay:443, and a
     DEDICATED fetcher interpreter -> the mail API. Deny the
     model-weights host now that the pull is done.
     Add explicit BLOCK rules for the OS network utilities: the
     "allow signed OS programs" default silently permits them.
  4. Negative test (REQUIRED, the lock-down is unproven without it):
       python3 -c "import socket;socket.create_connection(('1.1.1.1',443),timeout=5)"
         -> must FAIL, from every interpreter that matters
       ollama run <alias> "hello"
         -> must still WORK
  5. Install the launchd units from config/launchd/ (see its README).

Full detail and the traps: docs/deployment.md
================================================================
DONE
