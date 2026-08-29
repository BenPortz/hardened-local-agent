#!/usr/bin/env python3
"""Tier C — per-workflow digest (the primary review surface).

Reads the Tier A JSONL log and prints a terse, contextual summary of what a single workflow
run did. Run at the end of each routine, e.g.:

    python3 workflow_digest.py morning-email
    python3 workflow_digest.py morning-email --since 2026-06-22T07:00:00Z

Deterministic only — no LLM judgment. (An LLM may reformat this for readability elsewhere,
but must never be the safety gatekeeper.)
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
from pathlib import Path

DEFAULT_LOG = Path(os.environ.get("AUDIT_LOG", "./logs/audit.jsonl"))


def load_events(log_path: Path, workflow_id: str, since: str | None) -> list[dict]:
    if not log_path.exists():
        return []
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("workflow_id") != workflow_id:
            continue
        if since and e.get("timestamp", "") < since:
            continue
        events.append(e)
    return events


def render(workflow_id: str, events: list[dict]) -> str:
    if not events:
        return f"[{workflow_id}] no events found."
    times = [e["timestamp"] for e in events if e.get("timestamp")]
    span = f"{times[0][11:19]}–{times[-1][11:19]}" if times else "?"

    action_counts = Counter(e["action_type"] for e in events)
    hosts = Counter(e["external_host"] for e in events if e.get("external_host"))
    creds = Counter(e["credential_touched"] for e in events if e.get("credential_touched"))
    skills = Counter(e["skill"] for e in events if e.get("skill"))
    new_items = [e for e in events if e.get("new_vs_known") == "NEW"]
    tier_b = [e for e in events if e.get("tier_b_triggered")]
    awaiting = [e for e in events if e.get("result") == "awaiting_approval"]

    def fmt_counter(c: Counter) -> str:
        return ", ".join(f"{k} (x{v})" for k, v in c.items()) or "none"

    lines = [f"[{workflow_id} · {span}]  {len(events)} actions"]
    lines.append("  actions: " + fmt_counter(action_counts))
    lines.append("  skills: " + fmt_counter(skills))
    lines.append("  external hosts: " + fmt_counter(hosts))
    lines.append("  credentials: " + fmt_counter(creds))
    if awaiting:
        lines.append(f"  ⏳ AWAITING APPROVAL: {len(awaiting)} "
                     + "(" + ", ".join(e.get("target") or e["action_type"] for e in awaiting) + ")")
    if new_items:
        lines.append("  🆕 NEW: " + ", ".join(
            f"{e['action_type']}:{e.get('external_host') or e.get('skill') or e.get('credential_touched') or e.get('target')}"
            for e in new_items))
    lines.append(f"  {'⚠' if tier_b else '✓'} Tier B events: {len(tier_b)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_id")
    ap.add_argument("--since", default=None, help="ISO timestamp lower bound")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()
    events = load_events(Path(args.log), args.workflow_id, args.since)
    print(render(args.workflow_id, events))


if __name__ == "__main__":
    main()
