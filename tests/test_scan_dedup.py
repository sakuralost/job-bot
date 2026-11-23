from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import TelegramMessage
from app.services.scan_service import _content_duplicate


def _message(*, message_id: int, status: str, prompt_version: int | None):
    return TelegramMessage(
        telegram_chat_id=1,
        telegram_message_id=message_id,
        message_text="招聘远程产品经理",
        status=status,
        content_fingerprint="same-content",
        classification_prompt_version=prompt_version,
    )


def test_pending_message_blocks_duplicate_llm_call():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        pending = _message(message_id=1, status="pending_classification", prompt_version=None)
        db.add(pending)
        db.commit()
        assert _content_duplicate(db, "same-content", prompt_version=12).id == pending.id


def test_old_prompt_exclusion_does_not_block_new_prompt_review():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(_message(message_id=1, status="not_product_role", prompt_version=11))
        db.commit()
        assert _content_duplicate(db, "same-content", prompt_version=12) is None


def test_same_prompt_exclusion_is_deduplicated():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rejected = _message(message_id=1, status="not_product_role", prompt_version=12)
        db.add(rejected)
        db.commit()
        assert _content_duplicate(db, "same-content", prompt_version=12).id == rejected.id


def test_duplicate_of_old_prompt_rejection_does_not_block_new_prompt():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(_message(message_id=1, status="duplicate", prompt_version=11))
        db.commit()
        assert _content_duplicate(db, "same-content", prompt_version=12) is None
