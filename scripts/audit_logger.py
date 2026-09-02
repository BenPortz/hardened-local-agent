#!/usr/bin/env python3
"""Tier A: silent append-only audit log + new-vs-known classification.

The forensic source of truth. Every agent action is recorded here as one JSON object per
line (JSONL). Detection is deterministic: an action's host/skill/credential is checked
against the allowlists and flagged `known` or `NEW`. NEW + high-risk action => Tier B alert.

Stdlib only. Other tiers read the JSONL this writes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Action types that, when NEW or always (see HIGH_RISK_ALWAYS), warrant a Tier B alert.
HIGH_RISK_ALWAYS = {
    "email.send", "email.delete", "settings.change", "cred.access",
    "skill.run.first",   # a newly-created skill running for the first time
    "project.blocked",   # orchestrator step failed, needs human attention
    "ask.cloud",         # cloud escalation: content leaves the host, always Tier B
}
# These only alert when the target is NEW (not on an allowlist).
HIGH_RISK_IF_NEW = {"net.connect", "skill.create"}

DEFAULT_LOG = Path(os.environ.get("AUDIT_LOG", "./logs/audit.jsonl"))
ALLOWLIST_DIR = Path(os.environ.get("ALLOWLIST_DIR", "./allowlists"))


def _load_allowlist(name: str) -> set[str]:
    path = ALLOWLIST_DIR / name
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def classify(external_host: str | None, skill: str | None, credential: str | None) -> str:
    """Return 'known' if every referenced host/skill/credential is allowlisted, else 'NEW'."""
    hosts = _load_allowlist("hosts.txt")
    skills = _load_allowlist("skills.txt")
    creds = _load_allowlist("credentials.txt")
    if external_host and external_host not in hosts:
        return "NEW"
    if skill and skill not in skills:
        return "NEW"
    if credential and credential not in creds:
        return "NEW"
    return "known"


def should_alert(action_type: str, new_vs_known: str) -> bool:
    if action_type in HIGH_RISK_ALWAYS:
        return True
    if action_type in HIGH_RISK_IF_NEW and new_vs_known == "NEW":
        return True
    return False


def log_event(
    workflow_id: str,
    action_type: str,
    *,
    target: str | None = None,
    external_host: str | None = None,
    credential_touched: str | None = None,
    skill: str | None = None,
    payload_summary: str = "",
    result: str = "ok",
    log_path: Path | None = None,
) -> dict:
    """Append one event to the audit log and return it (with new_vs_known + tier_b flags)."""
    new_vs_known = classify(external_host, skill, credential_touched)
    tier_b = should_alert(action_type, new_vs_known)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "workflow_id": workflow_id,
        "action_type": action_type,
        "target": target,
        "external_host": external_host,
        "credential_touched": credential_touched,
        "skill": skill,
        "payload_summary": payload_summary,
        "new_vs_known": new_vs_known,
        "result": result,
        "tier_b_triggered": tier_b,
    }
    path = log_path or DEFAULT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    if tier_b:
        # Fire Tier B without making it a hard dependency (alerting must never block logging).
        try:
            from alert import notify  # type: ignore
            notify(event)
        except Exception:
            pass
    return event


if __name__ == "__main__":
    # Smoke test: writes a couple of demo events to ./logs/audit.jsonl
    print(log_event("demo", "email.read", target="inbox", external_host="gmail.googleapis.com",
                     credential_touched="gmail_readonly", payload_summary="read 3 emails"))
    print(log_event("demo", "net.connect", external_host="evil.example.com",
                     payload_summary="unexpected outbound"))  # should flag NEW + Tier B
