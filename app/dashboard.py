from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database.models import (
    JobContact,
    JobDedupGroup,
    JobPost,
    ManualScanRequest,
    MonitoredChat,
    ReclassificationRun,
    ScanSchedule,
    ScanRun,
    TelegramMessage,
)
from app.services.progress import format_beijing_time, progress_snapshot
from app.database.session import get_session
from app.services.prompt_service import (
    DEFAULT_SYSTEM_PROMPT,
    activate_prompt_version,
    get_active_prompt,
    prompt_history,
    save_prompt,
)
from app.services.scan_schedule import (
    MAX_DAILY_SCANS,
    normalize_scan_times,
    get_scan_schedule,
    save_scan_schedule,
    scan_schedule_view,
)
from worker.manual_scan import launch_manual_scan

router = APIRouter()
security = HTTPBasic()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ACTIVE_MANUAL_SCAN_STATUSES = {"queued", "scanning", "waiting_for_llm", "classifying"}
DISPLAY_STATES = {"new", "saved", "ignored", "contacted"}


def _contains_chinese(value: str | None) -> bool:
    return bool(value and any("\u4e00" <= character <= "\u9fff" for character in value))


def _display_reason(job: JobPost) -> str:
    if _contains_chinese(job.role_match_reason):
        return job.role_match_reason or ""
    work_mode = {
        "remote": "明确支持远程工作",
        "hybrid": "属于混合办公",
        "onsite": "需要现场办公",
        "unknown": "未说明办公方式",
    }.get(job.work_mode, "办公方式未明确")
    parts = ["识别为产品经理相关岗位", work_mode]
    if job.location:
        parts.append(f"地点为 {job.location}")
    parts.append(f"模型匹配度为 {round(job.confidence * 100)}%")
    return "；".join(parts)


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


def _list_scope(state: str, category: str) -> tuple[str, str, str]:
    """人工状态页优先于模型分类；普通分类页只展示尚未处理的岗位。"""
    if state in DISPLAY_STATES:
        return state, state, ""
    return "new", "", "app" if category == "app" else ""


def _workflow_snapshot(db) -> dict[str, object]:
    reclassification = db.scalar(
        select(ReclassificationRun).order_by(ReclassificationRun.id.desc()).limit(1)
    )
    manual = db.scalar(
        select(ManualScanRequest).order_by(ManualScanRequest.id.desc()).limit(1)
    )
    latest_scan = db.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))

    reclassification_data = None
    if reclassification is not None:
        progress = progress_snapshot(
            status=reclassification.status,
            total=reclassification.total_items,
            processed=reclassification.processed_items,
            started_at=reclassification.started_at,
        )
        reclassification_data = {
            "status": reclassification.status,
            "status_text": {
                "pending": "等待开始",
                "running": "正在筛选",
                "completed": "已完成",
                "superseded": "已被新提示词替代",
                "failed": "运行失败",
            }.get(reclassification.status, "状态未知"),
            "prompt_version": reclassification.prompt_version,
            "accepted": reclassification.accepted_items,
            "excluded": reclassification.excluded_items,
            "duplicate": reclassification.duplicate_items,
            "errors": reclassification.error_items,
            **progress,
        }

    manual_data = None
    if manual is not None:
        progress = progress_snapshot(
            status=manual.status,
            total=manual.classification_total,
            processed=manual.classification_processed,
            started_at=manual.llm_started_at,
        )
        manual_data = {
            "id": manual.id,
            "status": manual.status,
            "status_text": {
                "queued": "等待开始",
                "scanning": "正在读取 Telegram 新消息",
                "waiting_for_llm": "新消息已读取，正在等待本地模型空闲",
                "classifying": "正在用本地模型筛选新消息",
                "completed": "主动检测已完成",
                "failed": "主动检测未完成，请稍后重试或查看服务日志",
            }.get(manual.status, "状态未知"),
            "active": manual.status in ACTIVE_MANUAL_SCAN_STATUSES,
            "messages_found": manual.messages_found,
            "rule_candidates": manual.rule_candidates,
            "scan_errors": manual.scan_error_count,
            "accepted": manual.accepted_items,
            "excluded": manual.excluded_items,
            "duplicate": manual.duplicate_items,
            "errors": manual.error_items,
            "remaining": max(0, progress["total"] - progress["processed"]),
            "finished_at": format_beijing_time(manual.finished_at),
            **progress,
        }

    latest_scan_data = None
    if latest_scan is not None:
        latest_scan_data = {
            "status": latest_scan.status,
            "messages": latest_scan.total_messages,
            "candidates": latest_scan.rule_candidates,
            "errors": latest_scan.error_count,
            "finished_at": format_beijing_time(latest_scan.finished_at),
        }

    return {
        "reclassification": reclassification_data,
        "manual_scan": manual_data,
        "latest_scan": latest_scan_data,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = "",
    state: str = "",
    category: str = "",
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    stats = {
        "sources": db.scalar(select(func.count()).select_from(MonitoredChat).where(MonitoredChat.enabled.is_(True))),
        "messages": db.scalar(select(func.count()).select_from(TelegramMessage)),
        "duplicates": db.scalar(
            select(func.count()).select_from(TelegramMessage).where(TelegramMessage.status == "duplicate")
        ),
    }
    visible_jobs = (
        JobPost.is_excluded.is_(False),
        JobPost.duplicate_of_job_post_id.is_(None),
    )
    states = dict(
        db.execute(select(JobPost.status, func.count()).where(*visible_jobs).group_by(JobPost.status)).all()
    )
    total_jobs = states.get("new", 0)
    app_jobs = db.scalar(
        select(func.count()).select_from(JobPost).where(
            *visible_jobs,
            JobPost.is_app_product_role.is_(True),
            JobPost.status == "new",
        )
    )

    query_status, state, category = _list_scope(state, category)
    query = select(JobPost).where(*visible_jobs, JobPost.status == query_status)
    if category == "app":
        query = query.where(JobPost.is_app_product_role.is_(True))
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
        group = db.get(JobDedupGroup, job.dedup_group_id) if job.dedup_group_id is not None else None
        jobs.append({
            "id": job.id, "company_name": job.company_name, "job_title": job.job_title,
            "confidence": job.confidence, "status": job.status, "work_mode": job.work_mode,
            "is_app_product_role": job.is_app_product_role,
            "location": job.location, "salary": job.salary, "industry": job.industry,
            "role_match_reason": _display_reason(job), "job_summary": job.job_summary,
            "job_description": job.job_description, "contacts": contacts,
            "original_message": message.message_text if message else None,
            "source_link": chat.title if chat else None,
            "message_date": message.message_date if message else None,
            "classification_model": message.classification_model if message else None,
            "classification_prompt_version": message.classification_prompt_version if message else None,
            "ignore_reason": group.note if group and group.status == "ignored" else None,
            "sender": (f"@{message.sender_username}" if message and message.sender_username else (str(message.sender_telegram_user_id) if message and message.sender_telegram_user_id else None)),
            "source_url": _message_link(chat, message.telegram_message_id) if message else None,
        })

    latest_run = db.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
    workflow = _workflow_snapshot(db)
    scan_schedule = scan_schedule_view(get_scan_schedule(db))
    active_prompt = get_active_prompt()
    cfg = settings()
    list_title = {
        "saved": "已保留",
        "ignored": "已忽略",
        "contacted": "已联系",
    }.get(state, "待处理 APP 产品经理" if category == "app" else "待处理候选机会")
    return templates.TemplateResponse(request, "dashboard.html", {
        "stats": stats, "states": states, "total_jobs": total_jobs, "app_jobs": app_jobs, "jobs": jobs,
        "latest_run": latest_run, "q": q, "state": state, "category": category,
        "list_title": list_title,
        "workflow": workflow,
        "scan_schedule": scan_schedule,
        "active_prompt": active_prompt,
        "prompt_history": prompt_history(),
        "llm_model": cfg.openai_model or "尚未配置",
        "llm_ready": bool(cfg.openai_api_key and cfg.openai_model and cfg.openai_base_url),
    })


@router.get("/api/workflow-status")
def workflow_status(db=Depends(get_session), _: str = Depends(require_admin)):
    return _workflow_snapshot(db)


@router.post("/settings/scan-schedule")
def update_scan_schedule(
    scan_count: int = Form(...),
    scan_times: list[str] = Form(...),
    enabled: str | None = Form(None),
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    if not 1 <= scan_count <= MAX_DAILY_SCANS:
        raise HTTPException(422, f"每天检测次数必须在 1 到 {MAX_DAILY_SCANS} 次之间")
    try:
        values = normalize_scan_times(scan_times, expected_count=scan_count)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    save_scan_schedule(db, enabled=enabled == "on", scan_times=values)
    return RedirectResponse("/#scan-schedule", status_code=303)


@router.post("/scan-now")
def scan_now(db=Depends(get_session), _: str = Depends(require_admin)):
    active = db.scalar(
        select(ManualScanRequest)
        .where(ManualScanRequest.status.in_(ACTIVE_MANUAL_SCAN_STATUSES))
        .order_by(ManualScanRequest.id.desc())
        .limit(1)
    )
    if active is not None:
        return RedirectResponse("/#workflow-status", status_code=303)

    request = ManualScanRequest(status="queued", trigger_source="manual")
    db.add(request)
    try:
        db.commit()
        db.refresh(request)
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/#workflow-status", status_code=303)

    try:
        launch_manual_scan(request.id)
    except Exception as exc:  # noqa: BLE001 - 启动失败必须写入任务状态
        request.status = "failed"
        request.finished_at = datetime.now(timezone.utc)
        request.error_message = str(exc)[:2000]
        db.commit()
        raise HTTPException(500, "主动检测任务未能启动，请稍后重试") from exc
    return RedirectResponse("/#workflow-status", status_code=303)


@router.post("/opportunities/{job_id}/status")
def update_status(
    request: Request,
    job_id: int,
    value: str = Form(...),
    ignore_reason: str = Form(""),
    return_state: str = Form(""),
    return_category: str = Form(""),
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    if value not in {"new", "saved", "ignored", "contacted"}:
        raise HTTPException(422, "无效状态")
    normalized_reason = " ".join(ignore_reason.split())
    if value == "ignored" and not normalized_reason:
        raise HTTPException(422, "请填写忽略原因")
    if len(normalized_reason) > 500:
        raise HTTPException(422, "忽略原因不能超过 500 个字符")
    job = db.get(JobPost, job_id)
    if job is None:
        raise HTTPException(404, "未找到该岗位")
    previous_status = job.status
    job.status = value
    if job.dedup_group_id is not None:
        group = db.get(JobDedupGroup, job.dedup_group_id)
        if group is not None:
            group.status = value
            if value == "ignored":
                group.note = normalized_reason
    db.commit()
    if "application/json" in request.headers.get("accept", ""):
        visible_after_update = (
            value == return_state
            if return_state in DISPLAY_STATES
            else value == "new"
        )
        return JSONResponse({
            "ok": True,
            "job_id": job.id,
            "previous_status": previous_status,
            "status": value,
            "remove": not visible_after_update,
        })
    params = []
    if return_state in DISPLAY_STATES:
        params.append(f"state={return_state}")
    elif return_category == "app":
        params.append("category=app")
    target = "/?" + "&".join(params) if params else "/"
    return RedirectResponse(target, status_code=303)


@router.post("/settings/llm-prompt")
def update_llm_prompt(
    system_prompt: str = Form(...),
    change_note: str = Form(""),
    _: str = Depends(require_admin),
):
    try:
        save_prompt(system_prompt, change_note)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return RedirectResponse("/#llm-settings", status_code=303)


@router.post("/settings/llm-prompt/{prompt_id}/activate")
def reactivate_llm_prompt(prompt_id: int, _: str = Depends(require_admin)):
    try:
        activate_prompt_version(prompt_id)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return RedirectResponse("/#llm-settings", status_code=303)


@router.post("/settings/llm-prompt/reset")
def reset_llm_prompt(_: str = Depends(require_admin)):
    save_prompt(DEFAULT_SYSTEM_PROMPT, "恢复系统内置默认提示词")
    return RedirectResponse("/#llm-settings", status_code=303)
