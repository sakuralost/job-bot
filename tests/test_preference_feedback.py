from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import JobDedupGroup
from app.services.preference_feedback import ignored_reason_context


def test_only_ignored_reasons_are_added_to_new_job_prompt():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            JobDedupGroup(status="ignored", note="岗位偏增长方向"),
            JobDedupGroup(status="ignored", note="需要金融行业经验"),
            JobDedupGroup(status="saved", note="这是保留岗位备注"),
        ])
        db.commit()

        context = ignored_reason_context(db)

    assert "岗位偏增长方向" in context
    assert "需要金融行业经验" in context
    assert "这是保留岗位备注" not in context
    assert "不是无条件排除规则" in context
    assert "role_match_reason" in context


def test_duplicate_ignore_reasons_are_only_included_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            JobDedupGroup(status="ignored", note="不考虑增长产品"),
            JobDedupGroup(status="ignored", note="  不考虑增长产品  "),
        ])
        db.commit()

        context = ignored_reason_context(db)

    assert context.count("不考虑增长产品") == 1
