#!/usr/bin/env python3
"""Daily digest push — one morning notification that doubles as a liveness alarm.

Gathers service health (hub / ntfy / ollama / scheduler), project states, and a Tier D-style
roll-up of the last 24h of the audit log, then publishes it to the phone via alert.push().
The contract is the *absence* signal: this fires every morning at the same time, so a
missing digest means the host, its scheduler, the push server, or the network is down.

Runs under a scheduled service (see config/launchd/). Manual run:

    python3 scripts/daily_digest.py            # push to phone
    python3 scripts/daily_digest.py --dry-run  # print only

Deterministic only — no LLM judgment (see docs/monitoring-tiers.md).
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
import shutil
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import alert  # noqa: E402

AUDIT_LOG = REPO / "logs" / "audit.jsonl"
PROJECTS = REPO / "projects"
MODE_FILE = REPO / "config" / "mode.env"

# In production hub and ntfy bind the host's private-mesh interface (mesh-only by design)
# and HUB_HOST/NTFY_BASE point at it; the model server is loopback-only, always.
HUB_HOST = os.environ.get("HUB_HOST", "127.0.0.1")
SERVICES = {
    "hub": f"http://{HUB_HOST}:8787/api/status",
    "ntfy": alert.NTFY_BASE + "/v1/health",
    "ollama": "http://127.0.0.1:11434/v1/models",
}


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def scheduler_running() -> bool:
    """The scheduler holds an exclusive flock on logs/scheduler.lock while alive."""
    import fcntl
    lock_file = REPO / "logs" / "scheduler.lock"
    try:
        with lock_file.open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True  # someone (the scheduler) holds it
            fcntl.flock(f, fcntl.LOCK_UN)
            return False  # we got the lock, so nobody else holds it
    except OSError:
        return False  # no lock file / unreadable — scheduler can't be holding it


def mode() -> str:
    try:
        for line in MODE_FILE.read_text().splitlines():
            if line.strip().startswith("MODE="):
                return line.strip().split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "default"


def project_counts() -> Counter:
    counts: Counter = Counter()
    for p in PROJECTS.glob("*.json"):
        try:
            counts[json.loads(p.read_text()).get("state", "?")] += 1
        except Exception:
            counts["unreadable"] += 1
    return counts


def audit_rollup(hours: int = 24) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    events = []
    if AUDIT_LOG.exists():
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("timestamp", "") >= cutoff:
                events.append(e)
    return {
        "total": len(events),
        "steps": sum(1 for e in events if e.get("action_type") == "project.step"),
        "tier_b": sum(1 for e in events if e.get("tier_b_triggered")),
        "new_hosts": sorted({e["external_host"] for e in events
                             if e.get("new_vs_known") == "NEW" and e.get("external_host")}),
        "errors": sum(1 for e in events if e.get("result") == "error"),
    }


def render() -> tuple[str, str, str]:
    """Return (title, body, priority). Priority escalates if something is down."""
    svc = {name: http_ok(url) for name, url in SERVICES.items()}
    svc["scheduler"] = scheduler_running()
    projs = project_counts()
    roll = audit_rollup()
    free_gb = shutil.disk_usage("/").free // 10**9

    def mark(ok: bool) -> str:
        return "OK" if ok else "DOWN"

    all_up = all(svc.values())
    svc_line = " · ".join(f"{k} {mark(v)}" for k, v in svc.items())
    proj_line = (", ".join(f"{n} {state}" for state, n in sorted(projs.items()))
                 or "none")
    lines = [
        f"Services: {svc_line} (mode={mode()})",
        f"Projects: {proj_line}",
        f"Last 24h: {roll['total']} events · {roll['steps']} steps · "
        f"{roll['tier_b']} tier-B · {roll['errors']} errors",
        "New hosts: " + (", ".join(roll["new_hosts"]) or "none"),
        f"Disk free: {free_gb} GB",
    ]
    day = datetime.now().strftime("%a %Y-%m-%d")
    title = f"Agent digest — {day}" + ("" if all_up else " — SERVICE DOWN")
    return title, "\n".join(lines), "default" if all_up else "high"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    args = ap.parse_args()
    title, body, priority = render()
    print(f"{title}\n{body}")
    if not args.dry_run:
        alert.push(title, body, priority=priority,
                   tags="newspaper" if priority == "default" else "rotating_light")


if __name__ == "__main__":
    main()
