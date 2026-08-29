#!/usr/bin/env python3
"""One-time OAuth mint for the read-only mail fetcher (stdlib loopback + PKCE).

Run ONCE, in your own terminal on a machine with a browser, to turn a Desktop OAuth client
(client_secret.json) into token.json for gmail_fetch.py. It requests ONLY gmail.readonly and
REFUSES to save a token with any broader scope. The resulting token.json is machine-
independent: mint it on a workstation, then move it to the agent host's credential directory.

No secret is printed to stdout. Usage:
  python scripts/gmail_auth.py --client /path/client_secret.json --out /path/token.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _load_client(path: str) -> tuple[str, str]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    c = d.get("installed") or d.get("web")
    if not c:
        sys.exit("client_secret.json has no 'installed'/'web' section — is this a Desktop OAuth client?")
    return c["client_id"], c["client_secret"]


class _Catch(http.server.BaseHTTPRequestHandler):
    code = None
    state = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catch.code = params.get("code", [None])[0]
        _Catch.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorization received. You can close this tab.</h2>")

    def log_message(self, *a):
        pass


def mint(client_path: str, out_path: str) -> None:
    client_id, client_secret = _load_client(client_path)

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Catch)   # ephemeral loopback port
    port = srv.server_address[1]
    redirect = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)                     # PKCE
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    auth_url = AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Opening your browser for Google consent (read-only Gmail)...")
    print("If it doesn't open, paste this into your browser:\n" + auth_url + "\n")
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    for _ in range(300):                                     # up to 5 min for consent
        if _Catch.code:
            break
        time.sleep(1)
    if not _Catch.code:
        sys.exit("timed out waiting for consent")
    if _Catch.state != state:
        sys.exit("state mismatch — aborting (possible CSRF)")

    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "code": _Catch.code, "code_verifier": verifier,
        "grant_type": "authorization_code", "redirect_uri": redirect,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URI, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode())

    granted = tok.get("scope", SCOPE).split()
    bad = [s for s in granted if s != SCOPE]
    if bad:
        sys.exit(f"refusing to save: granted scopes are not read-only: {bad}")

    out = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tok.get("refresh_token"),
        "access_token": tok["access_token"],
        "expiry_epoch": time.time() + int(tok.get("expires_in", 3600)),
        "scopes": [SCOPE],
    }
    Path(out_path).write_text(json.dumps(out, indent=2))
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass

    if not out["refresh_token"]:
        print("WARNING: no refresh_token returned (Google returns one only on first consent).\n"
              "  Revoke this app at myaccount.google.com/permissions, then re-run to get one.")
    print(f"OK: wrote {out_path}  (scope: gmail.readonly). Keep this file private — do not share it.")


def main() -> None:
    ap = argparse.ArgumentParser(description="One-time read-only Gmail OAuth mint.")
    ap.add_argument("--client", required=True, help="path to client_secret_*.json (Desktop client)")
    ap.add_argument("--out", default="token.json", help="where to write token.json (keep OUT of the repo)")
    a = ap.parse_args()
    mint(a.client, a.out)


if __name__ == "__main__":
    main()
