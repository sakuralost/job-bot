from types import SimpleNamespace

import pytest

from app.services.source_management import (
    SOURCE_HISTORY_DAYS,
    ResolvedSource,
    normalize_source_reference,
    upsert_source,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://t.me/remote_jobs_demo", "remote_jobs_demo"),
        ("t.me/remote_jobs_demo", "remote_jobs_demo"),
        ("@remote_jobs_demo", "remote_jobs_demo"),
        ("remote_jobs_demo", "remote_jobs_demo"),
    ],
)
def test_normalize_public_source_reference(value, expected):
    assert normalize_source_reference(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/remote_jobs_demo",
        "https://t.me/+privateInvite",
        "https://t.me/remote_jobs_demo/123",
        "ab",
    ],
)
def test_reject_unsupported_source_reference(value):
    with pytest.raises(ValueError):
        normalize_source_reference(value)


def test_new_source_starts_from_three_day_baseline():
    class FakeDb:
        def __init__(self):
            self.added = None

        def scalar(self, _query):
            return None

        def add(self, row):
            self.added = row

    db = FakeDb()
    source, created = upsert_source(
        db,
        ResolvedSource(
            telegram_chat_id=-100123,
            title="HR 工作",
            username="remote_jobs_demo",
            initial_message_id=456,
        ),
    )
    assert SOURCE_HISTORY_DAYS == 3
    assert created is True
    assert db.added is source
    assert source.last_scanned_message_id == 456
    assert source.enabled is True


def test_reenabling_source_preserves_previous_cursor():
    existing = SimpleNamespace(
        telegram_chat_id=-100123,
        title="旧名称",
        username="remote_jobs_demo",
        enabled=False,
        last_scanned_message_id=900,
    )

    class FakeDb:
        def scalar(self, _query):
            return existing

        def add(self, _row):
            raise AssertionError("已有来源不应重复新增")

    source, created = upsert_source(
        FakeDb(),
        ResolvedSource(
            telegram_chat_id=-100123,
            title="新名称",
            username="remote_jobs_demo",
            initial_message_id=1200,
        ),
    )
    assert created is False
    assert source.enabled is True
    assert source.title == "新名称"
    assert source.last_scanned_message_id == 900
