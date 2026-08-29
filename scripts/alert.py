#!/usr/bin/env python3
"""Tier B — real-time alert for high-risk events.

Fires a desktop notification and a push to the owner's phone the moment a high-blast-radius
event is logged. Best-effort:
alerting must never block or crash the audit logger, so all failures are swallowed by callers.

High-risk classes (decided in audit_logger.py): outbound send, delete/settings change,
credential access, connection to a NEW external host, a new skill about to run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
try:  # ensure emoji/Unicode print on any console (Windows cp1252, etc.)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _short(event: dict) -> tuple[str, str]:
    title = f"Agent ⚠ {event.get('action_type', 'event')}"
    bits = []
    if event.get("new_vs_known") == "NEW":
        bits.append("NEW")
    for k in ("target", "external_host", "credential_touched", "skill"):
        if event.get(k):
            bits.append(f"{k}={event[k]}")
    if event.get("result") and event["result"] != "ok":
        bits.append(event["result"])
    body = f"[{event.get('workflow_id','?')}] " + " · ".join(bits)
    return title, body


# Self-hosted ntfy server. Bind it to the private-mesh interface in production and set
# NTFY_BASE accordingly; the loopback default keeps notifications on-box out of the tin.
NTFY_BASE = os.environ.get("NTFY_BASE", "http://127.0.0.1:2586")
NTFY_TOPIC_FILE = Path(os.environ.get("NTFY_TOPIC_FILE", Path.home() / ".agent" / "ntfy_topic"))


def push(title: str, body: str, priority: str = "high", tags: str = "warning") -> None:
    """Publish to the self-hosted ntfy server so the phone gets a push. Best-effort.
    Public: the orchestrator also uses this for clarification/done notifications."""
    try:
        topic = NTFY_TOPIC_FILE.read_text().strip()
        if not topic:
            return
        # HTTP headers are ascii-only: drop emoji etc. from the title, keep body UTF-8.
        ascii_title = " ".join(title.encode("ascii", "ignore").decode().split())
        req = urllib.request.Request(
            f"{NTFY_BASE}/{topic}", data=body.encode(),
            headers={"Title": ascii_title, "Priority": priority, "Tags": tags})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass  # alerting must never block or crash the caller


def notify(event: dict) -> None:
    """Push, plus a desktop notification where one is available. No-op on failure."""
    title, body = _short(event)
    push(title, body)
    # Prefer terminal-notifier if installed (richer); fall back to osascript.
    if shutil.which("terminal-notifier"):
        subprocess.run(
            ["terminal-notifier", "-title", title, "-message", body, "-sound", "default"],
            check=False,
        )
        return
    if shutil.which("osascript"):
        # AppleScript string literals are double-quoted; json.dumps escapes " and \ for us.
        b, t = (json.dumps(s, ensure_ascii=False) for s in (body, title))
        script = f'display notification {b} with title {t} sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
        return
    # Non-macOS (e.g. testing on Windows): print so the event is at least visible.
    print(f"[TIER-B ALERT] {title} — {body}")


if __name__ == "__main__":
    notify({"workflow_id": "demo", "action_type": "email.send", "target": "someone@example.com",
            "new_vs_known": "known", "result": "awaiting_approval"})
