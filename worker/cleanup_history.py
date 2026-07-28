from __future__ import annotations

import argparse
import json
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update

from app.database.models import (
    Contact,
    JobContact,
    JobDedupGroup,
    JobDedupKey,
    JobPost,
    NotificationRecord,
    ReclassificationItem,
    TelegramMessage,
)
from app.database.session import SessionLocal
from app.dedup.fingerprints import message_content_fingerprint


def _count(db, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def cleanup_before(db, cutoff: datetime) -> dict[str, int]:
    """删除截止时间前的消息及其附属记录，并为保留消息补齐正文指纹。"""
    target_message_ids = select(TelegramMessage.id).where(
        TelegramMessage.message_date < cutoff
    )
    target_job_ids = select(JobPost.id).where(
        JobPost.source_message_id.in_(target_message_ids)
    )
    stats = {
        "messages": _count(db, TelegramMessage, TelegramMessage.message_date < cutoff),
        "jobs": _count(db, JobPost, JobPost.source_message_id.in_(target_message_ids)),
        "job_contacts": _count(db, JobContact, JobContact.job_post_id.in_(target_job_ids)),
        "notifications": _count(
            db, NotificationRecord, NotificationRecord.job_post_id.in_(target_job_ids)
        ),
        "reclassification_items": _count(
            db,
            ReclassificationItem,
            or_(
                ReclassificationItem.message_id.in_(target_message_ids),
                ReclassificationItem.job_post_id.in_(target_job_ids),
            ),
        ),
    }

    db.execute(
        update(JobPost)
        .where(JobPost.duplicate_of_job_post_id.in_(target_job_ids))
        .values(duplicate_of_job_post_id=None)
    )
    db.execute(
        update(TelegramMessage)
        .where(TelegramMessage.duplicate_of_message_id.in_(target_message_ids))
        .values(duplicate_of_message_id=None)
    )
    db.execute(
        delete(ReclassificationItem).where(
            or_(
                ReclassificationItem.message_id.in_(target_message_ids),
                ReclassificationItem.job_post_id.in_(target_job_ids),
            )
        )
    )
    db.execute(delete(NotificationRecord).where(NotificationRecord.job_post_id.in_(target_job_ids)))
    db.execute(delete(JobContact).where(JobContact.job_post_id.in_(target_job_ids)))
    db.execute(delete(JobPost).where(JobPost.id.in_(target_job_ids)))
    db.execute(delete(TelegramMessage).where(TelegramMessage.id.in_(target_message_ids)))

    orphan_contact_ids = select(Contact.id).where(
        ~Contact.id.in_(select(JobContact.contact_id))
    )
    stats["orphan_contacts"] = _count(db, Contact, Contact.id.in_(orphan_contact_ids))
    db.execute(delete(Contact).where(Contact.id.in_(orphan_contact_ids)))

    orphan_group_ids = select(JobDedupGroup.id).where(
        ~JobDedupGroup.id.in_(
            select(JobPost.dedup_group_id).where(JobPost.dedup_group_id.is_not(None))
        ),
        ~JobDedupGroup.id.in_(
            select(TelegramMessage.dedup_group_id).where(
                TelegramMessage.dedup_group_id.is_not(None)
            )
        ),
    )
    stats["orphan_dedup_groups"] = _count(
        db, JobDedupGroup, JobDedupGroup.id.in_(orphan_group_ids)
    )
    db.execute(delete(JobDedupKey).where(JobDedupKey.group_id.in_(orphan_group_ids)))
    db.execute(delete(JobDedupGroup).where(JobDedupGroup.id.in_(orphan_group_ids)))

    fingerprint_rows = list(
        db.execute(
            select(TelegramMessage.id, TelegramMessage.message_text).where(
                TelegramMessage.content_fingerprint.is_(None)
            )
        )
    )
    for message_id, message_text in fingerprint_rows:
        db.execute(
            update(TelegramMessage)
            .where(TelegramMessage.id == message_id)
            .values(content_fingerprint=message_content_fingerprint(message_text))
        )
    stats["fingerprints_backfilled"] = len(fingerprint_rows)
    return stats


def parse_cutoff(value: str) -> datetime:
    cutoff = datetime.fromisoformat(value)
    if cutoff.tzinfo is None:
        raise ValueError("截止时间必须包含时区，例如 2026-07-01T00:00:00+08:00")
    return cutoff


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, help="带时区的 ISO 截止时间")
    parser.add_argument("--apply", action="store_true", help="实际提交；默认只预览")
    args = parser.parse_args()
    cutoff = parse_cutoff(args.before)

    with SessionLocal() as db:
        if args.apply:
            stats = cleanup_before(db, cutoff)
            db.commit()
        else:
            stats = {
                "messages": _count(
                    db, TelegramMessage, TelegramMessage.message_date < cutoff
                ),
                "jobs": _count(
                    db,
                    JobPost,
                    JobPost.source_message_id.in_(
                        select(TelegramMessage.id).where(
                            TelegramMessage.message_date < cutoff
                        )
                    ),
                ),
            }
    print(json.dumps({"applied": args.apply, "cutoff": cutoff.isoformat(), **stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
