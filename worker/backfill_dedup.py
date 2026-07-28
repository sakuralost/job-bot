from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import delete, select, update

from app.database.models import JobContact, JobDedupGroup, JobDedupKey, JobPost, ReclassificationRun, TelegramMessage
from app.database.session import SessionLocal
from app.dedup.fingerprints import job_dedup_keys
from app.dedup.service import MANUAL_STATUSES, canonical_job, record_occurrence, resolve_group


def _keys_for_job(db, job: JobPost, message: TelegramMessage) -> list[tuple[str, str]]:
    contacts = list(
        db.scalars(select(JobContact.contact_value).where(JobContact.job_post_id == job.id))
    )
    return job_dedup_keys(
        message_text=message.message_text,
        company=job.company_name,
        title=job.job_title,
        contacts=contacts,
        description=job.job_description,
    )


def import_legacy_decisions(db, sqlite_path: Path = Path("/app/data/job-bot.sqlite3")) -> dict[str, int]:
    counts = {"saved": 0, "ignored": 0, "contacted": 0}
    if not sqlite_path.exists():
        return counts
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT a.status, a.note, COALESCE(m.original_text, m.text) AS message_text
            FROM analyses a
            JOIN messages m ON m.id = a.message_id
            WHERE a.status IN ('saved', 'ignored', 'contacted')
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        keys = job_dedup_keys(
            message_text=row["message_text"],
            company=None,
            title=None,
            contacts=[],
            description=None,
        )
        resolve_group(db, keys, status=row["status"], note=row["note"])
        counts[row["status"]] += 1
    return counts


def backfill_existing_jobs() -> dict[str, int]:
    summary = {
        "checked": 0,
        "canonical": 0,
        "duplicates": 0,
        "legacy_saved": 0,
        "legacy_ignored": 0,
        "legacy_contacted": 0,
    }
    with SessionLocal.begin() as db:
        legacy = import_legacy_decisions(db)
        summary.update({f"legacy_{key}": value for key, value in legacy.items()})
        jobs = list(
            db.scalars(
                select(JobPost)
                .where(JobPost.is_excluded.is_(False))
                .order_by(JobPost.created_at, JobPost.id)
            )
        )
        for job in jobs:
            message = db.get(TelegramMessage, job.source_message_id)
            if message is None:
                continue
            status = job.status if job.status in MANUAL_STATUSES else "new"
            keys = _keys_for_job(db, job, message)
            preferred = db.get(JobDedupGroup, job.dedup_group_id) if job.dedup_group_id else None
            group, _ = resolve_group(db, keys, preferred=preferred, status=status)
            record_occurrence(group, message)
            job.dedup_group_id = group.id
            existing = canonical_job(db, group.id, excluding_job_id=job.id)
            if existing is None:
                job.duplicate_of_job_post_id = None
                job.status = group.status
                summary["canonical"] += 1
            else:
                job.duplicate_of_job_post_id = existing.id
                message.status = "duplicate"
                summary["duplicates"] += 1
            summary["checked"] += 1

        groups = list(db.scalars(select(JobDedupGroup)))
        for group in groups:
            canonical = canonical_job(db, group.id)
            if canonical is not None:
                canonical.status = group.status
    return summary


def reset_dedup_groups() -> None:
    """仅用于调整去重规则后重建索引；岗位和人工状态本身不会删除。"""
    with SessionLocal.begin() as db:
        running = db.scalar(
            select(ReclassificationRun.id).where(ReclassificationRun.status.in_(("pending", "running"))).limit(1)
        )
        if running is not None:
            raise RuntimeError(f"重新筛选任务 #{running} 仍在运行，不能重建去重索引")
        duplicate_message_ids = select(JobPost.source_message_id).where(
            JobPost.duplicate_of_job_post_id.is_not(None)
        )
        db.execute(
            update(TelegramMessage)
            .where(
                TelegramMessage.id.in_(duplicate_message_ids),
                TelegramMessage.status == "duplicate",
            )
            .values(status="classified")
        )
        db.execute(update(JobPost).values(duplicate_of_job_post_id=None, dedup_group_id=None))
        db.execute(update(TelegramMessage).values(dedup_group_id=None))
        db.execute(delete(JobDedupKey))
        db.execute(delete(JobDedupGroup))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    if args.rebuild:
        reset_dedup_groups()
    print(f"长期去重初始化完成：{backfill_existing_jobs()}", flush=True)
