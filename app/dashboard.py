from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select

from app.database.models import JobContact, JobPost, MonitoredChat, ScanRun, TelegramMessage
from app.database.session import get_session

router = APIRouter()
security = HTTPBasic()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    password = os.getenv("JOB_BOT_ADMIN_PASSWORD", "")
    if not password:
        raise HTTPException(status_code=503, detail="管理员密码尚未配置，服务拒绝开放访问")
    valid = secrets.compare_digest(credentials.username, "admin") and secrets.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _message_link(chat: MonitoredChat | None, message_id: int) -> str | None:
    if chat is None:
        return None
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    internal_id = str(chat.telegram_chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal_id}/{message_id}"


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", state: str = "", db=Depends(get_session), _: str = Depends(require_admin)):
    stats = {
        "sources": db.scalar(select(func.count()).select_from(MonitoredChat).where(MonitoredChat.enabled.is_(True))),
        "messages": db.scalar(select(func.count()).select_from(TelegramMessage)),
    }
    states = dict(db.execute(select(JobPost.status, func.count()).group_by(JobPost.status)).all())
    total_jobs = db.scalar(select(func.count()).select_from(JobPost).where(JobPost.is_excluded.is_(False)))

    query = select(JobPost).where(JobPost.is_excluded.is_(False))
    if state in {"new", "saved", "contacted", "ignored"}:
        query = query.where(JobPost.status == state)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(JobPost.job_title.ilike(like), JobPost.company_name.ilike(like), JobPost.job_summary.ilike(like)))
    order = {"new": 0, "saved": 1, "contacted": 2}
    rows = list(db.scalars(query.order_by(JobPost.created_at.desc()).limit(200)))
    rows.sort(key=lambda job: order.get(job.status, 3))

    jobs = []
    for job in rows:
        message = db.get(TelegramMessage, job.source_message_id)
        chat = db.scalar(select(MonitoredChat).where(MonitoredChat.telegram_chat_id == message.telegram_chat_id)) if message else None
        contacts = list(db.scalars(select(JobContact).where(JobContact.job_post_id == job.id)))
        jobs.append({
            "id": job.id, "company_name": job.company_name, "job_title": job.job_title,
            "confidence": job.confidence, "status": job.status, "work_mode": job.work_mode,
            "location": job.location, "salary": job.salary, "industry": job.industry,
            "role_match_reason": job.role_match_reason, "job_summary": job.job_summary,
            "job_description": job.job_description, "contacts": contacts,
            "source_link": chat.title if chat else None,
            "message_date": message.message_date if message else None,
            "sender": (f"@{message.sender_username}" if message and message.sender_username else (str(message.sender_telegram_user_id) if message and message.sender_telegram_user_id else None)),
            "source_url": _message_link(chat, message.telegram_message_id) if message else None,
        })

    latest_run = db.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
    return templates.TemplateResponse(request, "dashboard.html", {
        "stats": stats, "states": states, "total_jobs": total_jobs, "jobs": jobs,
        "latest_run": latest_run, "q": q, "state": state,
    })


@router.post("/opportunities/{job_id}/status")
def update_status(job_id: int, value: str = Form(...), return_state: str = Form(""), db=Depends(get_session), _: str = Depends(require_admin)):
    if value not in {"new", "saved", "ignored", "contacted"}:
        raise HTTPException(422, "无效状态")
    job = db.get(JobPost, job_id)
    if job is None:
        raise HTTPException(404, "未找到该岗位")
    job.status = value
    db.commit()
    return RedirectResponse(f"/?state={return_state}" if return_state else "/", status_code=303)
