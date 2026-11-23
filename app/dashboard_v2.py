from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .db import connect, init_db, preferences, save_preferences

security = HTTPBasic()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DEFAULT_LLM_PROMPT = """你是严格的 Telegram 招聘筛选器。先按岗位段落拆分多岗位消息，只保留产品经理相关段落；不得因为开发、运维、设计等其他岗位提高匹配度。命中博彩、色情、资金盘、跑分、灰产时排除。只返回 JSON：is_match、confidence、reason、matched_excerpt、job_title、contacts。"""


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    password = os.getenv("JOB_BOT_ADMIN_PASSWORD", "")
    if not password:
        raise HTTPException(status_code=503, detail="管理员密码尚未配置，服务拒绝开放访问")
    valid = secrets.compare_digest(credentials.username, "admin") and secrets.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="job-bot", lifespan=lifespan)


@app.get("/health")
def health():
    with connect() as conn:
        source_count = conn.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
    return {"status": "ok", "configured_sources": source_count, "telegram_ready": bool(os.getenv("TELEGRAM_API_ID") and os.getenv("TELEGRAM_API_HASH"))}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", state: str = "", source_id: int | None = None, _: str = Depends(require_admin)):
    with connect() as conn:
        stats = dict(conn.execute("""SELECT
          (SELECT COUNT(*) FROM sources WHERE enabled=1) AS sources,
          (SELECT COUNT(*) FROM messages) AS messages,
          (SELECT COUNT(*) FROM analyses WHERE is_match=1) AS matches,
          (SELECT MAX(finished_at) FROM runs WHERE status='success') AS last_success""").fetchone())
        filters, values = ["a.is_match=1"], []
        if q.strip():
            filters.append("(m.text LIKE ? OR m.sender_username LIKE ? OR s.label LIKE ?)")
            values.extend([f"%{q.strip()}%"] * 3)
        if state in {"new", "saved", "contacted", "ignored"}:
            filters.append("a.status=?")
            values.append(state)
        if source_id:
            filters.append("s.id=?")
            values.append(source_id)
        where = " AND ".join(filters)
        opportunities = conn.execute(f"""SELECT a.id, a.confidence, a.reason, a.status, a.note, m.text, m.sent_at,
          m.sender_username, m.sender_id, m.message_url, s.label
          FROM analyses a JOIN messages m ON m.id=a.message_id JOIN sources s ON s.id=m.source_id
          WHERE {where} ORDER BY CASE a.status WHEN 'new' THEN 0 WHEN 'saved' THEN 1 WHEN 'contacted' THEN 2 ELSE 3 END, a.analyzed_at DESC LIMIT 200""", values).fetchall()
        sources = conn.execute("SELECT * FROM sources ORDER BY label").fetchall()
        latest_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        states = dict(conn.execute("SELECT status, COUNT(*) FROM analyses WHERE is_match=1 GROUP BY status").fetchall())
    preference = preferences()
    preference.setdefault("llm_prompt", DEFAULT_LLM_PROMPT)
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "states": states, "opportunities": opportunities, "sources": sources, "preference": preference, "latest_run": latest_run, "q": q, "state": state, "source_id": source_id})


@app.post("/sources")
def add_source(handle: str = Form(...), label: str = Form(...), _: str = Depends(require_admin)):
    normalized = handle.strip().lstrip("@").replace("https://t.me/", "").strip("/")
    if not normalized:
        raise HTTPException(422, "来源不能为空")
    with connect() as conn:
        conn.execute("INSERT INTO sources(handle, label) VALUES(?, ?) ON CONFLICT(handle) DO UPDATE SET label=excluded.label", (normalized, label.strip() or normalized))
    return RedirectResponse("/", status_code=303)


@app.post("/preferences")
def update_preferences(role_keywords: str = Form(""), recruitment_keywords: str = Form(""), excluded_keywords: str = Form(""), llm_prompt: str = Form(""), _: str = Depends(require_admin)):
    split = lambda value: [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
    roles = split(role_keywords)
    recruitment = split(recruitment_keywords)
    save_preferences({"role_keywords": roles, "recruitment_keywords": recruitment, "required_any_groups": [roles, recruitment], "excluded_keywords": split(excluded_keywords), "llm_prompt": llm_prompt.strip()[:12000] or DEFAULT_LLM_PROMPT})
    return RedirectResponse("/", status_code=303)


@app.post("/opportunities/{analysis_id}/status")
def update_status(analysis_id: int, value: str = Form(...), note: str = Form(""), _: str = Depends(require_admin)):
    if value not in {"new", "saved", "ignored", "contacted"}:
        raise HTTPException(422, "无效状态")
    with connect() as conn:
        conn.execute("UPDATE analyses SET status=?, note=? WHERE id=?", (value, note.strip()[:300] or None, analysis_id))
    return RedirectResponse("/", status_code=303)
