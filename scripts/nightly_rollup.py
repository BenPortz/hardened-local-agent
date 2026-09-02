#!/usr/bin/env python3
"""Tier D: nightly roll-up (trends & anomalies across a day).

Aggregates the Tier A JSONL log for pattern-spotting, not per-action review. Run nightly:

    python3 nightly_rollup.py                 # today (UTC)
    python3 nightly_rollup.py --date 2026-06-22

Deterministic only. Highlights: skill-creation rate, new external hosts, credential-access
frequency, total outbound actions.
"""
from __future__ import annotations

import sys
try:  # ensure emoji/Unicode print on any console (Windows cp1252, etc.)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(os.environ.get("AUDIT_LOG", "./logs/audit.jsonl"))
OUTBOUND = {"email.send", "settings.change", "email.delete"}


def load_day(log_path: Path, day: str) -> list[dict]:
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("timestamp", "").startswith(day):
            out.append(e)
    return out


def render(day: str, events: list[dict]) -> str:
    if not events:
        return f"[nightly · {day}] no events."
    new_hosts = sorted({e["external_host"] for e in events
                        if e.get("new_vs_known") == "NEW" and e.get("external_host")})
    skills_created = [e for e in events if e["action_type"] == "skill.create"]
    cred_access = Counter(e["credential_touched"] for e in events
                         if e["action_type"] == "cred.access" and e.get("credential_touched"))
    outbound = [e for e in events if e["action_type"] in OUTBOUND]
    tier_b = [e for e in events if e.get("tier_b_triggered")]
    workflows = Counter(e["workflow_id"] for e in events)

    lines = [f"[nightly roll-up · {day}]  {len(events)} events across {len(workflows)} workflows"]
    lines.append(f"  outbound actions: {len(outbound)}")
    lines.append(f"  skills created: {len(skills_created)} "
                 + ("(" + ", ".join(e.get('target') or '?' for e in skills_created) + ")" if skills_created else ""))
    lines.append("  credential access: "
                 + (", ".join(f"{k} x{v}" for k, v in cred_access.items()) or "none"))
    lines.append("  🆕 new external hosts: " + (", ".join(new_hosts) if new_hosts else "none"))
    lines.append(f"  {'⚠' if tier_b else '✓'} Tier B events today: {len(tier_b)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()
    print(render(args.date, load_day(Path(args.log), args.date)))


if __name__ == "__main__":
    main()
