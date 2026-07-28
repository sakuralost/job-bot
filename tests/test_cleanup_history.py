from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import JobDedupGroup, JobDedupKey, JobPost, TelegramMessage
from worker.cleanup_history import cleanup_before


def test_cleanup_removes_old_history_and_backfills_retained_fingerprint():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with Session(engine) as db:
        group = JobDedupGroup(status="new")
        db.add(group)
        db.flush()
        group_id = group.id
        db.add(JobDedupKey(group_id=group.id, key_type="content", key_hash="old"))
        old_message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=1,
            message_text="旧招聘",
            message_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
            dedup_group_id=group.id,
        )
        current_message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=2,
            message_text="七月招聘",
            message_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        db.add_all([old_message, current_message])
        db.flush()
        db.add(
            JobPost(
                source_message_id=old_message.id,
                job_fingerprint="old",
                job_title="产品经理",
                is_product_role=True,
                is_excluded=False,
                confidence=0.9,
                dedup_group_id=group.id,
            )
        )
        db.commit()

        stats = cleanup_before(db, cutoff)
        db.commit()

        assert stats["messages"] == 1
        assert stats["jobs"] == 1
        assert stats["orphan_dedup_groups"] == 1
        messages = list(db.scalars(select(TelegramMessage)))
        assert [message.telegram_message_id for message in messages] == [2]
        assert messages[0].content_fingerprint
        assert db.get(JobDedupGroup, group_id) is None
