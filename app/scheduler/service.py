from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

from app.config import settings


def build_scheduler(schedule_check_callable) -> BackgroundScheduler:
    cfg = settings()
    timezone = ZoneInfo(cfg.timezone)
    scheduler = BackgroundScheduler(timezone=timezone)
    scheduler.add_job(
        schedule_check_callable,
        "interval",
        seconds=15,
        id="scan-schedule-check",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
