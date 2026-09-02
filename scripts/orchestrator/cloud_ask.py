#!/usr/bin/env python3
"""Optional cloud escalation, via SUBSCRIPTION CLIs only. No API keys.

claude  -> Claude Code CLI (`claude -p`), logged in with an existing subscription.
chatgpt -> Codex CLI (`codex exec`), logged in with an existing subscription.

Driving both through already-paid subscription CLIs means no API keys sit on the
agent host and usage hard-stops at the plan limit instead of billing per token.
When that (or anything else) fails, the scheduler falls back down the chain
cloud -> cloud -> local model, so a question always gets SOME answer.

This is a deliberate, per-question EXCEPTION to the local-only rule, so it is
narrow by construction: it sends EXACTLY the question the owner typed, with no local
context, no files, no history (content minimization, see docs/architecture.md).

Config via config/cloud_agents.env: CLOUD_DAILY_CAP (calls/day; a runaway-loop
backstop, not a spend cap, since subscription usage has no marginal cost).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CONFIG = REPO / "config" / "cloud_agents.env"
COUNTER = REPO / "logs" / "cloud_ask_counter.json"

HOSTS = {"claude": "api.anthropic.com", "chatgpt": "api.openai.com"}  # for audit records
CLI_BINARIES = {"claude": "claude", "chatgpt": "codex"}
CLI_SEARCH_DIRS = [Path.home() / ".local/bin", Path.home() / ".claude/local",
                   Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
DEFAULTS = {"CLOUD_DAILY_CAP": "20"}

SYSTEM = ("You are answering a single question relayed from a personal assistant "
          "system. Reply with the answer only: complete, direct, and concise. "
          "There is no follow-up conversation.")


class CloudAskError(Exception):
    """Raised with a user-facing message; callers surface it verbatim."""


def config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        for line in CONFIG.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def find_cli(agent: str) -> str | None:
    """Locate the subscription CLI; a service manager's PATH is minimal, so check known dirs."""
    name = CLI_BINARIES[agent]
    hit = shutil.which(name)
    if hit:
        return hit
    for d in CLI_SEARCH_DIRS:
        if (d / name).exists():
            return str(d / name)
    return None


def check_daily_cap(cap: int) -> None:
    """Increment today's counter; refuse once the cap is hit (runaway-loop backstop)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = {"date": today, "count": 0}
    try:
        loaded = json.loads(COUNTER.read_text())
        if loaded.get("date") == today:
            data = loaded
    except Exception:
        pass
    if data["count"] >= cap:
        raise CloudAskError(f"daily cloud-ask cap reached ({cap}/day; resets midnight UTC)")
    data["count"] += 1
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    COUNTER.write_text(json.dumps(data))


def _run_cli(cmd: list[str], timeout: int = 300) -> str:
    try:
        # stdin must be closed: codex (and possibly others) block reading a piped stdin
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise CloudAskError(f"subscription CLI timed out after {timeout}s")
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        # plan-limit exhaustion and auth problems surface here, so the scheduler
        # then falls down the chain (chatgpt -> local model) instead of dying
        err = (proc.stderr or out or "no output").strip()
        raise CloudAskError(f"CLI error: {err[:300]}")
    return out


def run(agent: str, question: str) -> str:
    """Entry point for the scheduler. Raises CloudAskError with a clear message."""
    if agent not in CLI_BINARIES:
        raise CloudAskError(f"unknown cloud agent '{agent}'")
    cli = find_cli(agent)
    if not cli:
        name = CLI_BINARIES[agent]
        raise CloudAskError(
            f"{agent} subscription CLI not installed. Install '{name}' on the agent host "
            "and log in with the subscription account")
    check_daily_cap(int(config()["CLOUD_DAILY_CAP"]))
    prompt = SYSTEM + "\n\nQUESTION: " + question
    if agent == "claude":
        return _run_cli([cli, "-p", prompt])
    # codex refuses to run outside a trusted/git directory without this flag
    return _run_cli([cli, "exec", "--skip-git-repo-check", prompt])
