from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def format_beijing_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).astimezone(BEIJING).strftime("%m 月 %d 日 %H:%M:%S")


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "正在计算"
    seconds = max(0, round(seconds))
    if seconds < 60:
        return "不到 1 分钟"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"约 {minutes} 分钟"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"约 {hours} 小时 {remaining_minutes} 分钟"
    days, remaining_hours = divmod(hours, 24)
    return f"约 {days} 天 {remaining_hours} 小时"


def progress_snapshot(
    *,
    status: str,
    total: int,
    processed: int,
    started_at: datetime | None,
    now: datetime | None = None,
) -> dict[str, object]:
    total = max(0, total or 0)
    processed = min(max(0, processed or 0), total) if total else max(0, processed or 0)
    percent = round(processed * 100 / total, 1) if total else (100.0 if status == "completed" else 0.0)
    remaining_seconds: float | None = None
    expected_finish: str | None = None

    if status in {"running", "classifying"} and started_at and total > 0 and 0 < processed < total:
        current = _aware(now or datetime.now(timezone.utc))
        elapsed = max(0.0, (current - _aware(started_at)).total_seconds())
        if elapsed > 0:
            remaining_seconds = (total - processed) * elapsed / processed
            expected = current + timedelta(seconds=remaining_seconds)
            expected_finish = expected.astimezone(BEIJING).strftime("%m 月 %d 日 %H:%M")

    return {
        "total": total,
        "processed": processed,
        "percent": percent,
        "remaining_text": "已完成" if status == "completed" else human_duration(remaining_seconds),
        "expected_finish": expected_finish,
    }
