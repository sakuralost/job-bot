from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from app.config import settings


def build_scheduler(scan_callable) -> BackgroundScheduler:
    cfg = settings()
    scheduler = BackgroundScheduler(timezone=ZoneInfo(cfg.timezone))
    for value in cfg.scan_times.split(","):
        hour, minute = value.strip().split(":")
        scheduler.add_job(scan_callable, CronTrigger(hour=int(hour), minute=int(minute)), id=f"scan-{hour}{minute}", max_instances=1, coalesce=True)
    return scheduler
