from datetime import datetime, timezone

from app.services.progress import format_beijing_time, human_duration, progress_snapshot


def test_progress_estimates_remaining_time_from_average_rate():
    snapshot = progress_snapshot(
        status="running",
        total=100,
        processed=25,
        started_at=datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc),
        now=datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc),
    )

    assert snapshot["percent"] == 25.0
    assert snapshot["remaining_text"] == "约 3 小时 0 分钟"
    assert snapshot["expected_finish"] == "07 月 27 日 12:00"


def test_progress_waits_for_first_processed_item_before_estimating():
    snapshot = progress_snapshot(
        status="running",
        total=100,
        processed=0,
        started_at=datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc),
        now=datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc),
    )

    assert snapshot["remaining_text"] == "正在计算"
    assert snapshot["expected_finish"] is None


def test_human_duration_uses_clear_chinese_units():
    assert human_duration(30) == "不到 1 分钟"
    assert human_duration(120) == "约 2 分钟"


def test_beijing_time_is_formatted_for_dashboard():
    value = datetime(2026, 7, 27, 10, 30, 15, tzinfo=timezone.utc)
    assert format_beijing_time(value) == "07 月 27 日 18:30:15"
