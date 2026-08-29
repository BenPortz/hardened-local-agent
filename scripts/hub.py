#!/usr/bin/env python3
"""Agent Hub — private-mesh API + phone dashboard over the orchestrator project queue.

Stdlib only (no pip). In production it binds the host's private-mesh (e.g. WireGuard/
Tailscale) address so it is reachable ONLY by the owner's own devices, never the LAN or the
public internet — verify with `lsof -i` after any change. Reads/writes the project records
defined in docs/orchestrator.md. Mutating endpoints require the token in $AGENT_HOME/hub_token
(X-Hub-Token header) — being on the mesh is NOT authentication. Every mutation is appended to
logs/hub-audit.jsonl (Tier A-style).

Run:  python3 hub.py            (env: HUB_HOST, HUB_PORT, AGENT_HOME override)
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECTS = REPO / "projects"
ASKS = REPO / "asks"
DRAFTS = REPO / "drafts"   # local-model email reply drafts (review-only, never sent)
# "local" is the default and never leaves the host. claude/chatgpt escalate to the cloud via
# scripts/orchestrator/cloud_ask.py — only the verbatim typed question leaves the host, under
# a daily call cap, audit-logged and pushed. See docs/architecture.md.
ASK_AGENTS = {"local", "claude", "chatgpt"}
AUDIT = REPO / "logs" / "hub-audit.jsonl"
AGENT_HOME = Path(os.environ.get("AGENT_HOME", Path.home() / ".agent"))
TOKEN_FILE = AGENT_HOME / "hub_token"
HOST = os.environ.get("HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("HUB_PORT", "8787"))
STATES = {"researching", "awaiting-input", "in-progress", "blocked", "done"}
START = time.time()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def token():
    return TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None


def audit(action, target, summary, result="ok"):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a") as f:
        f.write(json.dumps({
            "timestamp": now(), "workflow_id": "hub", "action_type": action,
            "target": target, "external_host": None, "credential_touched": None,
            "payload_summary": summary, "new_vs_known": "known", "result": result,
            "tier_b_triggered": False}) + "\n")


def load_projects():
    out = []
    PROJECTS.mkdir(exist_ok=True)
    for p in sorted(PROJECTS.glob("*.json")):
        try:
            rec = json.loads(p.read_text())
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
        except Exception:
            pass
    out.sort(key=lambda r: (r.get("state") != "awaiting-input", r.get("priority", 9)))
    return out


def save_project(rec):
    rec["updated"] = now()
    (PROJECTS / (rec["id"] + ".json")).write_text(json.dumps(rec, indent=2))


def load_asks(limit=30):
    out = []
    ASKS.mkdir(exist_ok=True)
    for p in ASKS.glob("*.json"):
        try:
            rec = json.loads(p.read_text())
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
        except Exception:
            pass
    out.sort(key=lambda r: r.get("created", ""), reverse=True)
    return out[:limit]


def save_ask(rec):
    ASKS.mkdir(exist_ok=True)
    rec["updated"] = now()
    (ASKS / (rec["id"] + ".json")).write_text(json.dumps(rec, indent=2))


def _parse_draft(out):
    """Split the local model's draft output into (subject, body) for display."""
    subj, body = "", ""
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        u = ln.strip().upper()
        if u.startswith("DRAFT_SUBJECT:"):
            subj = ln.split(":", 1)[1].strip()
        elif u.startswith("DRAFT_BODY:"):
            body = "\n".join(lines[i + 1:]).strip()
            break
        elif u.startswith("NO_REPLY_NEEDED:"):
            body = ln.split(":", 1)[1].strip()
    return subj, body


def load_drafts(limit=30):
    out = []
    DRAFTS.mkdir(exist_ok=True)
    for p in DRAFTS.glob("*.json"):
        try:
            rec = json.loads(p.read_text())
            if isinstance(rec, dict) and rec.get("id"):
                rec["subject"], rec["draft_body"] = _parse_draft(rec.get("model_output", ""))
                out.append(rec)
        except Exception:
            pass
    out.sort(key=lambda r: r.get("created", ""), reverse=True)
    return out[:limit]


def ollama_alive():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    base, i = s or "project", 2
    while (PROJECTS / (s + ".json")).exists():
        s, i = f"{base}-{i}", i + 1
    return s


def slugify_ask(question):
    return re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40] or "ask"


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentHub/0.1"

    def log_message(self, fmt, *args):  # quiet default access log
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        t = token()
        return bool(t) and self.headers.get("X-Hub-Token", "") == t

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 65536:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/favicon.ico":  # inline SVG icon is in <head>; silence the 404
            return self._send(204, b"", "image/x-icon")
        if self.path == "/api/status":
            projs = load_projects()
            return self._send(200, {
                "ok": True, "time": now(), "uptime_s": int(time.time() - START),
                "ollama": ollama_alive(), "projects": len(projs),
                "awaiting_input": sum(1 for p in projs if p.get("state") == "awaiting-input"),
                "last_updated": max((p.get("updated", "") for p in projs), default=None)})
        if self.path == "/api/projects":
            return self._send(200, load_projects())
        if self.path == "/api/asks":
            return self._send(200, load_asks())
        if self.path == "/api/drafts":
            return self._send(200, load_drafts())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "missing or bad X-Hub-Token"})
        body = self._body()
        if body is None:
            return self._send(400, {"error": "bad json"})

        if self.path == "/api/projects":
            title = (body.get("title") or "").strip()
            if not title:
                return self._send(400, {"error": "title required"})
            rec = {"id": slugify(title), "title": title, "state": "researching",
                   "priority": int(body.get("priority", 3)),
                   "clarifications": [], "notes": body.get("notes", ""), "updated": now()}
            save_project(rec)
            audit("project.create", rec["id"], f"enqueued via hub: {title[:80]}")
            return self._send(201, rec)

        if self.path == "/api/ask":
            question = (body.get("question") or "").strip()
            agent = (body.get("agent") or "local").strip().lower()
            if not question:
                return self._send(400, {"error": "question required"})
            if agent not in ASK_AGENTS:
                return self._send(501, {"error": f"agent '{agent}' is not enabled"})
            rec = {"id": f"{int(time.time())}-{slugify_ask(question)}", "agent": agent,
                   "question": question, "answer": None, "state": "asked",
                   "created": now()}
            save_ask(rec)
            audit("ask.create", rec["id"], f"[{agent}] {question[:80]}")
            return self._send(201, rec)

        m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/answer", self.path)
        if m:
            f = PROJECTS / (m.group(1) + ".json")
            if not f.exists():
                return self._send(404, {"error": "no such project"})
            answer = (body.get("answer") or "").strip()
            if not answer:
                return self._send(400, {"error": "answer required"})
            rec = json.loads(f.read_text())
            open_qs = [c for c in rec.get("clarifications", []) if not c.get("a")]
            if open_qs:
                open_qs[-1].update(a=answer, answered=now())
            else:
                rec.setdefault("clarifications", []).append(
                    {"q": None, "a": answer, "asked": None, "answered": now()})
            if rec.get("state") == "awaiting-input":
                rec["state"] = "in-progress"
            save_project(rec)
            audit("project.answer", rec["id"], f"answered via hub: {answer[:80]}")
            return self._send(200, rec)

        m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/state", self.path)
        if m:
            f = PROJECTS / (m.group(1) + ".json")
            new = body.get("state")
            if not f.exists():
                return self._send(404, {"error": "no such project"})
            if new not in STATES:
                return self._send(400, {"error": f"state must be one of {sorted(STATES)}"})
            rec = json.loads(f.read_text())
            rec["state"] = new
            save_project(rec)
            audit("project.state", rec["id"], f"state -> {new} via hub")
            return self._send(200, rec)

        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {"error": "missing or bad X-Hub-Token"})
        m = re.fullmatch(r"/api/drafts/([a-z0-9-]+)", self.path)
        if m:
            f = DRAFTS / (m.group(1) + ".json")
            if not f.exists():
                return self._send(404, {"error": "no such draft"})
            f.unlink()
            audit("draft.delete", m.group(1), "deleted via hub")
            return self._send(200, {"deleted": m.group(1)})
        # Only terminal-state records are deletable — the scheduler may hold in-flight ones.
        for pattern, folder, done_states, action in (
                (r"/api/asks/([a-z0-9-]+)", ASKS, {"answered", "failed"}, "ask.delete"),
                (r"/api/projects/([a-z0-9-]+)", PROJECTS, {"done", "blocked"}, "project.delete")):
            m = re.fullmatch(pattern, self.path)
            if not m:
                continue
            f = folder / (m.group(1) + ".json")
            if not f.exists():
                return self._send(404, {"error": "no such record"})
            rec = json.loads(f.read_text())
            if rec.get("state") not in done_states:
                return self._send(409, {"error": f"only {sorted(done_states)} can be deleted"})
            f.unlink()
            audit(action, m.group(1), f"deleted via hub (state={rec.get('state')})")
            return self._send(200, {"deleted": m.group(1)})
        return self._send(404, {"error": "not found"})


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Hub</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230B0F1A'/%3E%3Ctext x='16' y='23' font-size='19' font-weight='800' font-family='system-ui' text-anchor='middle' fill='%23F7E23F'%3EH%3C/text%3E%3C/svg%3E">
<style>
/* Cyberpunk palette: neon blue = main, yellow = accent. Mobile-first — the phone is the
   primary approval surface, and the same page serves the desktop. */
:root{--bg:#0B0F1A;--card:#131A2A;--ink:#E8EDF7;--mut:#8B96B0;--acc:#38CCFF;
--warn:#F7E23F;--line:#26304A}
*{box-sizing:border-box}body{margin:0;color:var(--ink);
background:radial-gradient(1100px 520px at 15% -8%,#13223E 0%,var(--bg) 55%) fixed var(--bg);
font:16px/1.5 -apple-system,system-ui,sans-serif}
.wrap{max-width:560px;margin:0 auto;padding:16px 14px 60px}
h1{font-size:22px;margin:4px 0 2px;letter-spacing:.02em;color:var(--acc)}
h1 b{font-weight:800}
.sub{color:var(--mut);font-size:13px;margin:0 0 16px}
.dot{display:inline-block;width:9px;height:9px;border-radius:99px;background:#777;
margin-right:6px;vertical-align:1px}
.dot.ok{background:var(--acc);box-shadow:0 0 8px var(--acc)}
.dot.bad{background:#F27;box-shadow:0 0 8px #F27}
.card{background:linear-gradient(180deg,#172136 0%,var(--card) 100%);
border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 12px}
.chip{font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
padding:2px 9px;border-radius:99px;background:#242E48;color:var(--mut)}
.chip.awaiting-input{background:#37320F;color:var(--warn)}
.chip.in-progress,.chip.researching{background:#0F3049;color:var(--acc)}
.chip.done{background:#1B2438;color:#8CA6D8}.chip.blocked{background:#3A2233;color:#F27}
.chip.asked{background:#37320F;color:var(--warn)}
.chip.answered{background:#0F3049;color:var(--acc)}.chip.failed{background:#3A2233;color:#F27}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.title{font-weight:600}.meta{color:var(--mut);font-size:12px;margin-top:2px}
.q{margin:10px 0 6px;padding:10px;border-left:3px solid var(--warn);
background:#211E0E;border-radius:0 8px 8px 0;font-size:14px}
textarea,input,button,select{font:inherit;border-radius:9px;border:1px solid var(--line);
background:#0F1524;color:var(--ink);padding:10px;width:100%}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.12em;color:var(--warn);
margin:24px 0 10px;padding-left:9px;border-left:3px solid var(--warn);
text-shadow:0 0 10px rgba(247,226,63,.3)}
select{width:auto;flex:0 0 auto}form.add .row input{margin-bottom:0}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--warn);
box-shadow:0 0 10px rgba(247,226,63,.25)}
button.y{background:var(--warn);color:#1C1903;box-shadow:0 0 14px rgba(247,226,63,.28)}
button.del{width:auto;flex:0 0 auto;background:none;border:0;box-shadow:none;margin:0;
padding:2px 7px;color:var(--mut);font-size:15px;line-height:1}button.del:active{color:#F27}
/* neon rails down the page edges: accent yellow */
body::before,body::after{content:"";position:fixed;top:0;bottom:0;width:2px;z-index:9;
pointer-events:none;background:var(--warn);box-shadow:0 0 10px rgba(247,226,63,.45)}
body::before{left:0}
body::after{right:0}
/* section identity: projects are blue-main, asks are yellow-main with blue accents */
#proj .card{border-left:3px solid var(--acc)}
#ask .card{border-left:3px solid var(--warn)}
#drafts .card{border-left:3px solid var(--warn)}
#drafts .notes{color:var(--ink);background:#0F1524;border:1px solid var(--line);
border-radius:8px;padding:10px;margin-top:8px}
#ask h2{color:var(--acc);border-left-color:var(--acc);
text-shadow:0 0 10px rgba(56,204,255,.35)}
#ask input:focus,#ask select:focus{border-color:var(--acc);
box-shadow:0 0 10px rgba(56,204,255,.3)}
#ask .chip.asked{background:#0F3049;color:var(--acc)}
#ask .chip.answered{background:#37320F;color:var(--warn)}
button{background:var(--acc);color:#04101C;font-weight:700;border:0;cursor:pointer;
margin-top:8px;box-shadow:0 0 14px rgba(56,204,255,.28)}button:active{opacity:.8}
h1{text-shadow:0 0 18px rgba(56,204,255,.35)}
form.add{margin:0 0 16px}form.add input{margin-bottom:8px}
.notes{color:var(--mut);font-size:13px;white-space:pre-wrap;margin-top:6px}
</style></head><body><div class="wrap">
<h1><span class="dot" id="dot"></span>Agent <b>Hub</b></h1>
<p class="sub" id="status">connecting…</p>
<section id="proj"><h2>Projects</h2>
<form class="add" onsubmit="return addProject(event)">
<input id="ttl" placeholder="Hand the agent a new project…" required>
<button>Add to queue</button></form>
<div id="list"></div></section>
<section id="drafts"><h2>Email drafts · review only</h2>
<div id="draftlist"></div></section>
<section id="ask"><h2>Ask an agent</h2>
<form class="add" onsubmit="return askQ(event)">
<div class="row"><select id="agent">
<option value="local">local (on-device)</option>
<option value="claude">claude (cloud)</option>
<option value="chatgpt">chatgpt (cloud)</option>
</select><input id="qq" placeholder="Ask a question…" required></div>
<button class="y">Ask</button></form>
<div id="asks"></div></section></div>
<script>
const $=s=>document.querySelector(s);
function tok(){let t=localStorage.hubToken;if(!t){t=prompt('Hub token (one-time setup):')||'';
localStorage.hubToken=t}return t}
async function api(p,opt){const r=await fetch(p,opt);if(r.status===401){
localStorage.removeItem('hubToken');alert('Bad token — try again');throw 0}return r.json()}
async function refresh(){try{
const s=await api('/api/status');
$('#dot').className='dot '+(s.ok&&s.ollama?'ok':'bad');
$('#status').textContent=`brain ${s.ollama?'online':'OFFLINE'} · ${s.projects} projects · `+
`${s.awaiting_input} waiting on you · up ${Math.floor(s.uptime_s/60)}m`;
const asks=await api('/api/asks');
$('#asks').innerHTML=asks.map(a=>`<div class="card">
<div class="row"><span class="title">${esc(a.question)}</span>
<span class="chip ${a.state}">${a.state}</span>
${['answered','failed'].includes(a.state)?`<button class="del" title="Delete"
onclick="delRec('asks','${a.id}')">✕</button>`:''}</div>
<div class="meta">${esc(a.agent)} · ${esc((a.created||'').replace('T',' ').slice(0,16))}</div>
${a.answer?`<div class="notes">${esc(a.answer)}</div>`:''}</div>`).join('')
||'<p class="sub">No questions asked yet.</p>';
const dr=await api('/api/drafts');
$('#draftlist').innerHTML=dr.map(d=>`<div class="card">
<div class="row"><span class="title">${esc(d.subject||d.term||'Draft')}</span>
<span class="chip ${d.kind==='no_reply'?'answered':'awaiting-input'}">${d.kind==='no_reply'?'no reply needed':'draft'}</span>
<button class="del" title="Delete" onclick="delRec('drafts','${d.id}')">✕</button></div>
<div class="meta">${esc((d.created||'').replace('T',' ').slice(0,16))} · not sent</div>
${d.draft_body?`<div class="notes">${esc(d.draft_body)}</div>`:''}
${d.kind!=='no_reply'?`<div class="meta" style="margin-top:8px">Copy into your mail app to send — auto-send is gated.</div>`:''}
</div>`).join('')||'<p class="sub">No drafts yet.</p>';
const ps=await api('/api/projects');
// Never re-render the list while the user is composing an answer: innerHTML replacement
// would wipe the textarea (and dismiss the phone keyboard) mid-typing.
const typing=[...document.querySelectorAll('#list textarea')].some(t=>t.value.trim()||t===document.activeElement);
if(typing)return;
$('#list').innerHTML=ps.map(p=>{
const q=(p.clarifications||[]).filter(c=>!c.a).slice(-1)[0];
return `<div class="card"><div class="row"><span class="title">${esc(p.title)}</span>
<span class="chip ${p.state}">${p.state}</span>
${['done','blocked'].includes(p.state)?`<button class="del" title="Delete"
onclick="delRec('projects','${p.id}')">✕</button>`:''}</div>
<div class="meta">updated ${esc((p.updated||'').replace('T',' ').slice(0,16))}</div>
${p.notes?`<div class="notes">${esc(p.notes)}</div>`:''}
${q?`<div class="q">❓ ${esc(q.q||'Agent is waiting for input')}</div>
<textarea id="a-${p.id}" placeholder="Your answer…"></textarea>
<button onclick="answer('${p.id}')">Send answer & resume</button>`:''}</div>`}).join('')
||'<p class="sub">Queue is empty — add a project above.</p>';
}catch(e){$('#dot').className='dot bad';$('#status').textContent='hub unreachable'}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';')}
async function addProject(ev){ev.preventDefault();
await api('/api/projects',{method:'POST',headers:{'X-Hub-Token':tok(),
'Content-Type':'application/json'},body:JSON.stringify({title:$('#ttl').value})});
$('#ttl').value='';refresh();return false}
async function delRec(kind,id){if(!confirm('Delete this permanently?'))return;
await api(`/api/${kind}/${id}`,{method:'DELETE',headers:{'X-Hub-Token':tok()}});refresh()}
async function askQ(ev){ev.preventDefault();
await api('/api/ask',{method:'POST',headers:{'X-Hub-Token':tok(),
'Content-Type':'application/json'},body:JSON.stringify({question:$('#qq').value,
agent:$('#agent').value})});
$('#qq').value='';refresh();return false}
async function answer(id){const el=document.getElementById('a-'+id);const v=el.value.trim();if(!v)return;
await api(`/api/projects/${id}/answer`,{method:'POST',headers:{'X-Hub-Token':tok(),
'Content-Type':'application/json'},body:JSON.stringify({answer:v})});
el.value='';el.blur();refresh()}
refresh();setInterval(refresh,15000);
</script></body></html>"""


if __name__ == "__main__":
    if not token():
        sys.exit(f"No token file at {TOKEN_FILE} — create it first (see docs/deployment.md).")
    print(f"Agent Hub on http://{HOST}:{PORT}  (projects: {PROJECTS})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
