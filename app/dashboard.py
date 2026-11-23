from __future__ import annotations

import os
import secrets
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
    WebJobListing,
    WebSource,
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
from app.services.source_management import (
    SOURCE_HISTORY_DAYS,
    SourceResolutionError,
    resolve_source,
    source_payload,
    upsert_source,
)
from app.services.web_source_management import web_source_payload
from app.services.worker_lock import telegram_scan_lock
from worker.manual_scan import launch_manual_scan

router = APIRouter()
security = HTTPBasic()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ACTIVE_MANUAL_SCAN_STATUSES = {"queued", "scanning", "waiting_for_llm", "classifying"}
DISPLAY_STATES = {"new", "saved", "ignored", "contacted"}
BEIJING = ZoneInfo("Asia/Shanghai")


def _contains_chinese(value: str | None) -> bool:
    return bool(value and any("\u4e00" <= character <= "\u9fff" for character in value))


def _chinese_friendly_label(level: str | None) -> str | None:
    return {
        "explicit_chinese": "明确中文友好",
        "no_english_requirement": "未要求英语",
        "needs_confirmation": "语言待确认",
        "english_required": "英语硬性要求",
    }.get(level or "")



def _web_source_label(listing: WebJobListing) -> str:
    if listing.source == "web3.career":
        return "Web3.career"
    provider, _, board = listing.source.partition(":")
    provider_label = {"greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "官方招聘"}.get(provider, provider)
    return f"{provider_label} · {listing.company_name or board}"
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


def _job_still_pending(job: tuple | None) -> bool:
    """待处理列表只收未排除、未去重、状态仍是 new 的职位。"""
    if job is None:
        return False
    status, is_excluded, duplicate_of = job
    return status == "new" and not is_excluded and duplicate_of is None


def _new_message_bucket(status: str, job: tuple | None) -> str:
    """本轮新消息只走一条漏斗：新增、招聘、去重、模型筛掉、有效新增。"""
    if status == "rule_rejected":
        return "not_recruitment"
    if status == "duplicate" or (job is not None and job[2] is not None):
        return "duplicate"
    if status == "pending_classification":
        return "awaiting_model"
    if status in {"excluded", "not_product_role", "error"}:
        return "model_rejected"
    if status == "classified":
        if _job_still_pending(job):
            return "valid_new"
        if job is not None and job[0] == "ignored":
            return "ignored"
        if job is not None and job[1]:
            return "model_rejected"
        if job is not None and job[2] is not None:
            return "duplicate"
        return "handled"
    return "not_recruitment"


def _filter_reason_label(status: str, result: object) -> str:
    """把模型内部分类归并成后台可读的原因，不展示原消息或完整模型解释。"""
    payload = result if isinstance(result, dict) else {}
    category = str(payload.get("excluded_category") or "").strip()
    category_lower = category.lower()
    if status == "classified":
        return "通过模型筛选"
    if status == "duplicate":
        return "重复岗位或重复消息"
    if status == "not_product_role":
        if "language" in category_lower or "语言" in category:
            return "语言要求不符合"
        if "top交易所" in category_lower or "交易所" in category:
            return "命中交易所排除规则"
        if "grey" in category_lower or "灰色" in category or "泛娱乐" in category:
            return "灰色产业或高风险方向"
        return "不是产品岗位"
    if status == "excluded":
        if "grey" in category_lower or "灰色" in category or "泛娱乐" in category:
            return "灰色产业或泛娱乐"
        if "product_subcategory" in category_lower or "产品细分" in category:
            return "产品细分方向不符合"
        if "sub_role" in category_lower or "细分" in category:
            return "岗位细分方向不符合"
        return "命中排除规则"
    if status == "error":
        return "模型处理错误"
    return "其他处理结果"


def _date_filter_bounds(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    start_dt = datetime(start.year, start.month, start.day, tzinfo=BEIJING)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=BEIJING) + timedelta(days=1)
    return start_dt.astimezone(timezone.utc), end_dt.astimezone(timezone.utc)


def _format_filter_window(start: datetime, end: datetime, selected: bool = False) -> str:
    if selected:
        end = end - timedelta(minutes=1)
    start_text = start.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    end_text = end.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    return f"{start_text} 至 {end_text}"


def _filter_stats(
    db,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, object]:
    """统计最近一次扫描或指定日期范围内的规则初筛和本地模型复核结果。"""
    empty = {
        "window": "暂无扫描记录",
        "start_date": "",
        "end_date": "",
        "selection_mode": "latest",
        "runs": 0,
        "new_messages": 0,
        "recruitment_messages": 0,
        "duplicates_removed": 0,
        "model_rejected": 0,
        "valid_new": 0,
        "awaiting_model": 0,
        "ignored_after": 0,
        "outcomes": [],
        "reasons": [],
        "note": "暂无可用扫描记录",
    }
    if not hasattr(db, "execute"):
        return empty

    latest_request = db.scalar(
        select(ManualScanRequest)
        .where(ManualScanRequest.status == "completed")
        .order_by(ManualScanRequest.id.desc())
        .limit(1)
    )
    selection_mode = "selected" if start_date or end_date else "latest"
    latest_scan_id = None
    if selection_mode == "selected":
        if not start_date or not end_date:
            raise ValueError("开始日期和结束日期需要同时填写")
        window_start, window_end = _date_filter_bounds(start_date, end_date)
    elif latest_request and latest_request.started_at and latest_request.finished_at:
        window_start = latest_request.started_at
        window_end = latest_request.finished_at
        latest_scan_id = latest_request.scan_run_id
        start_date = window_start.astimezone(BEIJING).date().isoformat()
        end_date = window_end.astimezone(BEIJING).date().isoformat()
    else:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=3)
        window_end = now

    if latest_scan_id is not None:
        run_count = 1 if db.get(ScanRun, latest_scan_id) is not None else 0
    else:
        run_count = db.scalar(
            select(func.count()).select_from(ScanRun).where(
                ScanRun.started_at >= window_start,
                ScanRun.started_at < window_end,
            )
        ) or 0
    message_rows = db.execute(
        select(
            TelegramMessage.id,
            TelegramMessage.status,
            TelegramMessage.classification_result_json,
        ).where(
            TelegramMessage.created_at >= window_start,
            TelegramMessage.created_at < window_end,
        )
    ).all()
    message_ids = [row[0] for row in message_rows]
    jobs_by_message: dict[int, tuple] = {}
    if message_ids:
        for source_message_id, job_status, is_excluded, duplicate_of in db.execute(
            select(
                JobPost.source_message_id,
                JobPost.status,
                JobPost.is_excluded,
                JobPost.duplicate_of_job_post_id,
            ).where(JobPost.source_message_id.in_(message_ids))
        ):
            jobs_by_message[source_message_id] = (job_status, is_excluded, duplicate_of)
    buckets = Counter()
    reasons = Counter()
    for message_id, status, result in message_rows:
        bucket = _new_message_bucket(status, jobs_by_message.get(message_id))
        buckets[bucket] += 1
        if bucket == "model_rejected":
            reasons[_filter_reason_label(status, result)] += 1
        elif bucket == "duplicate":
            reasons["重复的招聘信息"] += 1
        elif bucket == "ignored":
            reasons["已忽略，不计有效新增"] += 1
    new_messages = len(message_rows)
    recruitment_messages = new_messages - buckets["not_recruitment"]
    duplicates_removed = buckets["duplicate"]
    model_rejected = buckets["model_rejected"]
    valid_new = buckets["valid_new"]
    awaiting_model = buckets["awaiting_model"]
    ignored_after = buckets["ignored"]
    flow = [
        {"label": "新增消息", "count": new_messages},
        {"label": "招聘信息", "count": recruitment_messages},
        {"label": "去掉重复", "count": duplicates_removed},
        {"label": "模型筛掉", "count": model_rejected},
        {"label": "有效新增", "count": valid_new},
    ]
    reason_items = [
        {"label": label, "count": count}
        for label, count in reasons.most_common()
        if count
    ]
    note_parts = [
        "默认显示最近一次完成的扫描。" if selection_mode == "latest" else "已按所选日期汇总。",
        "只统计关注频道本轮新入库的消息。",
        "招聘信息去掉重复、再去掉模型判定不符合的，剩下的是有效新增。",
    ]
    if awaiting_model:
        note_parts.append(f"另有 {awaiting_model} 条招聘信息还在等模型，完成前不计有效新增。")
    if ignored_after:
        note_parts.append(f"另有 {ignored_after} 条已忽略，不计有效新增。")
    return {
        "window": ("上次扫描：" if selection_mode == "latest" else "所选日期：")
        + _format_filter_window(window_start, window_end, selection_mode == "selected"),
        "start_date": start_date or window_start.astimezone(BEIJING).date().isoformat(),
        "end_date": end_date or window_end.astimezone(BEIJING).date().isoformat(),
        "selection_mode": selection_mode,
        "runs": run_count,
        "new_messages": new_messages,
        "recruitment_messages": recruitment_messages,
        "duplicates_removed": duplicates_removed,
        "model_rejected": model_rejected,
        "valid_new": valid_new,
        "awaiting_model": awaiting_model,
        "ignored_after": ignored_after,
        "outcomes": flow,
        "reasons": reason_items,
        "note": "".join(note_parts),
    }


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
        "filter_stats": _filter_stats(db),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = "",
    state: str = "",
    category: str = "",
    language: str = "",
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    telegram_source_count = db.scalar(select(func.count()).select_from(MonitoredChat).where(MonitoredChat.enabled.is_(True)))
    web_source_count = db.scalar(select(func.count()).select_from(WebSource).where(WebSource.enabled.is_(True)))
    stats = {
        "sources": telegram_source_count + web_source_count,
        "telegram_sources": telegram_source_count,
        "web_sources": web_source_count,
        "messages": db.scalar(select(func.count()).select_from(TelegramMessage)),
        "duplicates": db.scalar(
            select(func.count()).select_from(TelegramMessage).where(TelegramMessage.status == "duplicate")
        ),
    }
    telegram_sources = list(db.scalars(select(MonitoredChat).order_by(MonitoredChat.enabled.desc(), MonitoredChat.title, MonitoredChat.id)))
    web_sources = list(db.scalars(select(WebSource).order_by(WebSource.enabled.desc(), WebSource.title, WebSource.id)))
    visible_jobs = (
        JobPost.is_excluded.is_(False),
        JobPost.duplicate_of_job_post_id.is_(None),
    )
    states = dict(
        db.execute(select(JobPost.status, func.count()).where(*visible_jobs).group_by(JobPost.status)).all()
    )
    total_jobs = states.get("new", 0)
    chinese_friendly_jobs = db.scalar(
        select(func.count()).select_from(JobPost).where(
            *visible_jobs,
            JobPost.status == "new",
            JobPost.chinese_friendly_level.in_(("explicit_chinese", "no_english_requirement")),
        )
    )
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
    if language == "chinese":
        query = query.where(JobPost.chinese_friendly_level.in_(("explicit_chinese", "no_english_requirement")))
    elif language == "confirmation":
        query = query.where(JobPost.chinese_friendly_level == "needs_confirmation")
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(JobPost.job_title.ilike(like), JobPost.company_name.ilike(like), JobPost.job_summary.ilike(like)))
    order = {"new": 0, "saved": 1, "contacted": 2}
    rows = list(db.scalars(query.order_by(JobPost.created_at.desc()).limit(200)))
    rows.sort(key=lambda job: order.get(job.status, 3))

    jobs = []
    for job in rows:
        message = db.get(TelegramMessage, job.source_message_id) if job.source_message_id is not None else None
        listing = db.get(WebJobListing, job.source_web_listing_id) if job.source_web_listing_id is not None else None
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
            "original_message": message.message_text if message else (listing.description if listing else None),
            "source_link": chat.title if chat else (_web_source_label(listing) if listing else None),
            "message_date": message.message_date if message else (listing.published_at if listing else None),
            "classification_model": message.classification_model if message else (listing.classification_model if listing else None),
            "classification_prompt_version": message.classification_prompt_version if message else (listing.classification_prompt_version if listing else None),
            "ignore_reason": group.note if group and group.status == "ignored" else None,
            "sender": (f"@{message.sender_username}" if message and message.sender_username else (str(message.sender_telegram_user_id) if message and message.sender_telegram_user_id else None)),
            "source_url": _message_link(chat, message.telegram_message_id) if message else (listing.source_url if listing else None),
            "apply_url": listing.apply_url if listing else None,
            "chinese_friendly_label": _chinese_friendly_label(job.chinese_friendly_level),
            "chinese_friendly_reason": job.chinese_friendly_reason,
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
        "stats": stats, "states": states, "total_jobs": total_jobs, "app_jobs": app_jobs,
        "chinese_friendly_jobs": chinese_friendly_jobs, "jobs": jobs,
        "telegram_sources": telegram_sources,
        "web_sources": web_sources,
        "source_history_days": SOURCE_HISTORY_DAYS,
        "latest_run": latest_run, "q": q, "state": state, "category": category, "language": language,
        "list_title": list_title,
        "workflow": workflow,
        "scan_schedule": scan_schedule,
        "active_prompt": active_prompt,
        "prompt_history": prompt_history(),
        "llm_model": cfg.openai_model or "尚未配置",
        "llm_ready": bool(cfg.openai_api_key and cfg.openai_model and cfg.openai_base_url),
    })


@router.post("/sources")
def add_source(
    source_ref: str = Form(...),
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    with telegram_scan_lock(blocking=False) as acquired:
        if not acquired:
            raise HTTPException(409, "正在读取 Telegram 消息，请稍后再添加来源")
        try:
            resolved = resolve_source(source_ref)
        except (ValueError, SourceResolutionError) as exc:
            raise HTTPException(422, str(exc)) from exc

    source, created = upsert_source(db, resolved)
    db.commit()
    db.refresh(source)
    enabled_count = db.scalar(
        select(func.count()).select_from(MonitoredChat).where(MonitoredChat.enabled.is_(True))
    )
    return JSONResponse({
        "ok": True,
        "created": created,
        "source": source_payload(source),
        "enabled_count": enabled_count,
        "history_days": SOURCE_HISTORY_DAYS if created else 0,
    })


@router.post("/sources/{source_id}/toggle")
def toggle_telegram_source(
    source_id: int,
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    source = db.get(MonitoredChat, source_id)
    if source is None:
        raise HTTPException(404, "未找到该来源")
    source.enabled = not source.enabled
    db.commit()
    enabled_count = db.scalar(
        select(func.count()).select_from(MonitoredChat).where(MonitoredChat.enabled.is_(True))
    )
    return JSONResponse({"ok": True, "source": source_payload(source), "enabled_count": enabled_count})


@router.post("/web-sources/{source_id}/toggle")
def toggle_web_source(
    source_id: int,
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    source = db.get(WebSource, source_id)
    if source is None:
        raise HTTPException(404, "未找到该网站来源")
    source.enabled = not source.enabled
    db.commit()
    return JSONResponse({"ok": True, "source": web_source_payload(source)})


@router.get("/api/workflow-status")
def workflow_status(db=Depends(get_session), _: str = Depends(require_admin)):
    return _workflow_snapshot(db)


@router.get("/api/filter-stats")
def filter_stats(
    start_date: str | None = None,
    end_date: str | None = None,
    db=Depends(get_session),
    _: str = Depends(require_admin),
):
    try:
        return _filter_stats(db, start_date=start_date, end_date=end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
