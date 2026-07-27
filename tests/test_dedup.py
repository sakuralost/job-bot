from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import JobDedupGroup, JobPost, TelegramMessage
from app.dedup.fingerprints import job_dedup_keys
from app.dedup.service import canonical_job, record_occurrence, resolve_group


def test_content_key_ignores_case_spacing_and_punctuation():
    first = job_dedup_keys(
        message_text="Hiring: Product Manager\nRemote!",
        company=None,
        title=None,
        contacts=[],
        description=None,
    )
    second = job_dedup_keys(
        message_text="ＨＩＲＩＮＧ product manager，remote",
        company=None,
        title=None,
        contacts=[],
        description=None,
    )
    assert first[0] == second[0]


def test_ignored_group_participates_in_future_deduplication():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        old_keys = job_dedup_keys(
            message_text="A 公司招聘远程产品经理",
            company="A 公司",
            title="产品经理",
            contacts=["@recruiter"],
            description="负责产品规划",
        )
        group, _ = resolve_group(db, old_keys, status="ignored", note="用户已忽略")
        old_message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=1,
            message_text="A 公司招聘远程产品经理",
        )
        db.add(old_message)
        db.flush()
        record_occurrence(group, old_message)
        old_job = JobPost(
            source_message_id=old_message.id,
            job_fingerprint="old",
            job_title="产品经理",
            company_name="A 公司",
            is_product_role=True,
            is_excluded=False,
            confidence=0.9,
            status=group.status,
            dedup_group_id=group.id,
        )
        db.add(old_job)
        db.flush()

        repost_keys = job_dedup_keys(
            message_text="全新排版，但仍是同一岗位",
            company="Ａ公司",
            title="产品经理",
            contacts=["@another-contact"],
            description="岗位描述发生少量变化",
        )
        matched, created = resolve_group(db, repost_keys)
        assert not created
        assert matched.id == group.id
        assert matched.status == "ignored"
        assert canonical_job(db, matched.id).id == old_job.id
        assert db.get(JobDedupGroup, group.id).note == "用户已忽略"
