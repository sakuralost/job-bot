from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.scheduler.service import build_scheduler
from app.services.scan_schedule import normalize_scan_times, scan_schedule_view


def test_schedule_checker_uses_configured_timezone():
    scheduler = build_scheduler(lambda: None)
    job = scheduler.get_jobs()[0]
    assert str(scheduler.timezone) == "Asia/Shanghai"
    assert job.id == "scan-schedule-check"
    assert job.trigger.interval.total_seconds() == 15


def test_scan_times_are_validated_sorted_and_unique():
    assert normalize_scan_times(["18:30", "04:00"], expected_count=2) == ["04:00", "18:30"]


def test_scan_schedule_view_is_ready_for_admin_form():
    row = SimpleNamespace(enabled=True, scan_times=["04:00", "18:30"])
    view = scan_schedule_view(row)
    assert view["count"] == 2
    assert view["summary"] == "04:00、18:30"
