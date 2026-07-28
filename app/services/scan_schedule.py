from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database.models import ManualScanRequest, ScanSchedule
from app.database.session import SessionLocal
from worker.manual_scan import ACTIVE_STATUSES, launch_manual_scan

MAX_DAILY_SCANS = 12
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def normalize_scan_times(values: list[str], expected_count: int | None = None) -> list[str]:
    normalized = [value.strip() for value in values if value.strip()]
    if expected_count is not None and expected_count != len(normalized):
        raise ValueError("每天检测次数与填写的时间数量不一致")
    if not normalized:
        raise ValueError("至少设置一个自动检测时间")
    if len(normalized) > MAX_DAILY_SCANS:
        raise ValueError(f"每天最多设置 {MAX_DAILY_SCANS} 次自动检测")
    if any(not TIME_PATTERN.fullmatch(value) for value in normalized):
        raise ValueError("检测时间格式不正确")
    if len(set(normalized)) != len(normalized):
        raise ValueError("自动检测时间不能重复")
    return sorted(normalized)


def _default_times() -> list[str]:
    try:
        return normalize_scan_times(settings().scan_times.split(","))
    except ValueError:
        return ["04:00"]


def get_scan_schedule(db) -> ScanSchedule:
    row = db.get(ScanSchedule, 1)
    if row is None:
        row = ScanSchedule(id=1, enabled=True, scan_times=_default_times())
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def save_scan_schedule(db, *, enabled: bool, scan_times: list[str]) -> ScanSchedule:
    row = get_scan_schedule(db)
    row.enabled = enabled
    row.scan_times = normalize_scan_times(scan_times)
    db.commit()
    db.refresh(row)
    return row


def scan_schedule_view(row: ScanSchedule) -> dict[str, object]:
    values = normalize_scan_times(list(row.scan_times or _default_times()))
    return {
        "enabled": row.enabled,
        "scan_times": values,
        "count": len(values),
        "summary": "、".join(values),
        "max_count": MAX_DAILY_SCANS,
    }


def enqueue_due_scan(now: datetime | None = None) -> int | None:
    timezone = ZoneInfo(settings().timezone)
    current = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    current_time = current.strftime("%H:%M")
    current_slot = current.strftime("%Y-%m-%dT%H:%M")
    request_id: int | None = None

    with SessionLocal() as db:
        schedule = db.scalar(
            select(ScanSchedule).where(ScanSchedule.id == 1).with_for_update()
        )
        if schedule is None:
            schedule = get_scan_schedule(db)
        times = normalize_scan_times(list(schedule.scan_times or _default_times()))
        if not schedule.enabled or current_time not in times:
            return None
        if schedule.last_triggered_slot == current_slot:
            return None
        schedule.last_triggered_slot = current_slot

        active = db.scalar(
            select(ManualScanRequest)
            .where(ManualScanRequest.status.in_(ACTIVE_STATUSES))
            .order_by(ManualScanRequest.id.desc())
            .limit(1)
        )
        if active is None:
            request = ManualScanRequest(status="queued", trigger_source="scheduled")
            db.add(request)
            try:
                db.commit()
                db.refresh(request)
                request_id = request.id
            except IntegrityError:
                db.rollback()
                request_id = None
        else:
            db.commit()

    if request_id is not None:
        launch_manual_scan(request_id)
    return request_id
