from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.classifier.schemas import JobClassificationResult
from app.database.base import Base
from app.database.models import JobDedupGroup, JobPost, TelegramMessage
from app.services.prompt_service import PromptSnapshot
from worker import reclassify_postgres


def test_new_prompt_rechecks_previously_excluded_canonical_jobs(monkeypatch):
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        group = JobDedupGroup(status="new")
        db.add(group)
        db.flush()
        grouped_message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=1,
            message_text="招聘产品经理",
        )
        historical_message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=2,
            message_text="历史排除消息",
        )
        db.add_all([grouped_message, historical_message])
        db.flush()
        db.add_all(
            [
                JobPost(
                    source_message_id=grouped_message.id,
                    job_fingerprint="grouped",
                    is_product_role=True,
                    is_excluded=True,
                    status="excluded",
                    confidence=0.9,
                    dedup_group_id=group.id,
                ),
                JobPost(
                    source_message_id=historical_message.id,
                    job_fingerprint="historical",
                    is_product_role=True,
                    is_excluded=True,
                    status="excluded",
                    confidence=0.9,
                ),
            ]
        )

    monkeypatch.setattr(reclassify_postgres, "SessionLocal", factory)
    run = reclassify_postgres.create_run(
        PromptSnapshot(
            id=8,
            version=8,
            system_prompt="测试提示词",
            change_note=None,
            created_at=None,
        )
    )
    assert run.total_items == 1


def test_manual_ignored_status_survives_later_model_exclusion():
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    prompt = PromptSnapshot(
        id=8,
        version=8,
        system_prompt="测试提示词",
        change_note=None,
        created_at=None,
    )
    result = JobClassificationResult(
        is_job_post=True,
        is_product_role=True,
        is_excluded=True,
        excluded_reason="模型重新筛选后排除",
        confidence=0.9,
        reasoning_summary="测试",
    )

    with factory.begin() as db:
        group = JobDedupGroup(status="ignored")
        message = TelegramMessage(
            telegram_chat_id=1,
            telegram_message_id=1,
            message_text="招聘 APP 产品经理",
        )
        db.add_all([group, message])
        db.flush()
        job = JobPost(
            source_message_id=message.id,
            job_fingerprint="manual-ignored",
            is_product_role=True,
            is_app_product_role=True,
            is_excluded=False,
            status="ignored",
            confidence=0.9,
            dedup_group_id=group.id,
        )
        db.add(job)
        db.flush()

        outcome = reclassify_postgres._apply_reclassification(
            db,
            job,
            message,
            result,
            prompt=prompt,
            model_name="test-model",
        )

        assert outcome == "excluded"
        assert job.status == "ignored"
        assert job.is_excluded is False
