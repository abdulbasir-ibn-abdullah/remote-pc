"""
Remote-Fix Relay (backend)
PC agenti va operator (Claude) o'rtasidagi HTTPS ko'prik.

Rollar va tokenlar:
  - ADMIN_KEY      : faqat egasida. Qisqa muddatli operator token yaratadi.
  - operator token : /admin/session orqali yaratiladi (default 30 daqiqa), chatga beriladi.
  - AGENT_TOKEN    : PC dagi agentda saqlanadi.

Oqim:
  operator  POST /cmd            -> buyruq navbatga tushadi
  agent     GET  /next (long-poll) -> buyruqni oladi, egasidan tasdiq so'raydi, bajaradi
  agent     POST /result/{id}    -> natija
  operator  GET  /result/{id}?wait=25 -> natijani oladi
"""
import asyncio
import hmac
import json
import os
import secrets
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(BASE / ".env")

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
if len(ADMIN_KEY) < 24 or len(AGENT_TOKEN) < 24:
    raise SystemExit("ADMIN_KEY va AGENT_TOKEN kamida 24 belgi bo'lishi kerak (.env ni tekshiring)")

MAX_TTL_MIN = 120
DEFAULT_TTL_MIN = 30
MAX_PENDING = 20
MAX_OUTPUT = 100_000
KEEP_COMMANDS = 200
AGENT_ONLINE_SEC = 40
LOG_FILE = BASE / "relay.log"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

operator_tokens: dict[str, float] = {}
commands: dict[str, dict] = {}
queue: deque[str] = deque()
agent_last_seen: float = 0.0
failed: dict[str, list[float]] = {}


def log(event: str, **data) -> None:
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def bearer(h: str) -> str:
    return h[7:].strip() if h.startswith("Bearer ") else ""


def ip_of(request: Request) -> str:
    return request.client.host if request.client else "?"


def guard(request: Request) -> str:
    ip = ip_of(request)
    now = time.time()
    lst = [t for t in failed.get(ip, []) if now - t < 300]
    failed[ip] = lst
    if len(lst) >= 10:
        raise HTTPException(429, "Too many failed attempts")
    return ip


def reject(ip: str):
    failed.setdefault(ip, []).append(time.time())
    log("auth_fail", ip=ip)
    raise HTTPException(401, "Unauthorized")


def auth_admin(request: Request, x_admin_key: str = Header(default="")):
    ip = guard(request)
    if not x_admin_key or not same(x_admin_key, ADMIN_KEY):
        reject(ip)


def auth_agent(request: Request, authorization: str = Header(default="")):
    ip = guard(request)
    tok = bearer(authorization)
    if not tok or not same(tok, AGENT_TOKEN):
        reject(ip)


def auth_operator(request: Request, authorization: str = Header(default="")):
    ip = guard(request)
    tok = bearer(authorization)
    now = time.time()
    for t in [t for t, exp in operator_tokens.items() if exp < now]:
        operator_tokens.pop(t, None)
    ok = False
    for t in operator_tokens:  # barcha tokenlarni solishtiramiz (constant-time)
        if tok and same(tok, t):
            ok = True
    if not ok:
        reject(ip)


def trim_output(s: str) -> str:
    if len(s) <= MAX_OUTPUT:
        return s
    head, tail = int(MAX_OUTPUT * 0.6), int(MAX_OUTPUT * 0.4)
    return s[:head] + f"\n...[{len(s) - MAX_OUTPUT} belgi qisqartirildi]...\n" + s[-tail:]


def prune() -> None:
    if len(commands) > KEEP_COMMANDS:
        old = sorted(commands.items(), key=lambda kv: kv[1]["created"])
        for cid, _ in old[: len(commands) - KEEP_COMMANDS]:
            commands.pop(cid, None)


class CmdIn(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    timeout: int = Field(60, ge=1, le=600)


class ResultIn(BaseModel):
    status: Literal["done", "rejected", "error"]
    exit_code: Optional[int] = None
    output: str = Field("", max_length=400_000)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/admin/session", dependencies=[Depends(auth_admin)])
async def admin_session(ttl_minutes: int = Query(DEFAULT_TTL_MIN, ge=1, le=MAX_TTL_MIN)):
    token = secrets.token_urlsafe(32)
    operator_tokens[token] = time.time() + ttl_minutes * 60
    log("session_created", ttl_minutes=ttl_minutes)
    return {"token": token, "expires_in_minutes": ttl_minutes}


@app.post("/admin/revoke", dependencies=[Depends(auth_admin)])
async def admin_revoke():
    """Kill switch: barcha operator tokenlari va kutayotgan buyruqlar bekor qilinadi."""
    operator_tokens.clear()
    cancelled = 0
    while queue:
        c = commands.get(queue.popleft())
        if c and c["status"] == "pending":
            c["status"] = "rejected"
            c["output"] = "revoked by admin"
            cancelled += 1
    log("revoked", cancelled=cancelled)
    return {"revoked": True, "cancelled_commands": cancelled}


@app.get("/status", dependencies=[Depends(auth_operator)])
async def status():
    return {
        "agent_online": (time.time() - agent_last_seen) < AGENT_ONLINE_SEC,
        "pending": len(queue),
    }


@app.post("/cmd", dependencies=[Depends(auth_operator)])
async def post_cmd(body: CmdIn):
    if len(queue) >= MAX_PENDING:
        raise HTTPException(429, "Queue full")
    cid = uuid.uuid4().hex[:12]
    commands[cid] = {
        "id": cid,
        "command": body.command,
        "timeout": body.timeout,
        "status": "pending",
        "exit_code": None,
        "output": "",
        "created": time.time(),
    }
    queue.append(cid)
    prune()
    log("cmd_queued", id=cid, command=body.command)
    return {"id": cid}


@app.get("/next", dependencies=[Depends(auth_agent)])
async def next_cmd(wait: int = Query(20, ge=0, le=30)):
    global agent_last_seen
    deadline = time.time() + wait
    while True:
        agent_last_seen = time.time()
        while queue:
            cid = queue.popleft()
            c = commands.get(cid)
            if c and c["status"] == "pending":
                c["status"] = "delivered"
                return {"id": cid, "command": c["command"], "timeout": c["timeout"]}
        if time.time() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(0.5)


@app.post("/result/{cid}", dependencies=[Depends(auth_agent)])
async def post_result(cid: str, body: ResultIn):
    c = commands.get(cid)
    if not c or c["status"] != "delivered":
        raise HTTPException(404, "Unknown or not delivered")
    c["status"] = body.status
    c["exit_code"] = body.exit_code
    c["output"] = trim_output(body.output)
    log("cmd_result", id=cid, status=body.status, exit_code=body.exit_code)
    return {"ok": True}


@app.get("/result/{cid}", dependencies=[Depends(auth_operator)])
async def get_result(cid: str, wait: int = Query(0, ge=0, le=30)):
    deadline = time.time() + wait
    while True:
        c = commands.get(cid)
        if not c:
            raise HTTPException(404, "Unknown id")
        if c["status"] in ("done", "rejected", "error") or time.time() >= deadline:
            return {k: c[k] for k in ("id", "command", "status", "exit_code", "output")}
        await asyncio.sleep(0.5)
