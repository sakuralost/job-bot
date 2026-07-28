from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from app.database.models import JobDedupGroup, JobDedupKey, JobPost, TelegramMessage

DedupKey = tuple[str, str]
MANUAL_STATUSES = {"saved", "ignored", "contacted"}
_STATUS_PRIORITY = {"new": 0, "ignored": 1, "saved": 2, "contacted": 3}


def _status_priority(status: str) -> int:
    return _STATUS_PRIORITY.get(status, 0)


def matching_groups(db, keys: list[DedupKey]) -> list[JobDedupGroup]:
    group_ids: list[int] = []
    for key_type, key_hash in keys:
        group_id = db.scalar(
            select(JobDedupKey.group_id).where(
                JobDedupKey.key_type == key_type,
                JobDedupKey.key_hash == key_hash,
            )
        )
        if group_id is not None and group_id not in group_ids:
            group_ids.append(group_id)
    return [group for group_id in group_ids if (group := db.get(JobDedupGroup, group_id)) is not None]


def _merge_groups(db, target: JobDedupGroup, source: JobDedupGroup) -> None:
    if source.id == target.id:
        return
    if _status_priority(source.status) > _status_priority(target.status):
        target.status = source.status
        target.note = source.note
    elif not target.note and source.note:
        target.note = source.note
    target.occurrence_count += source.occurrence_count
    target.first_seen_at = min(target.first_seen_at, source.first_seen_at)
    target.last_seen_at = max(target.last_seen_at, source.last_seen_at)

    target_canonical = db.scalar(
        select(JobPost).where(
            JobPost.dedup_group_id == target.id,
            JobPost.duplicate_of_job_post_id.is_(None),
        ).order_by(JobPost.id)
    )
    source_canonicals = list(
        db.scalars(
            select(JobPost).where(
                JobPost.dedup_group_id == source.id,
                JobPost.duplicate_of_job_post_id.is_(None),
            ).order_by(JobPost.id)
        )
    )
    if target_canonical is None and source_canonicals:
        target_canonical = source_canonicals.pop(0)
    if target_canonical is not None:
        for job in source_canonicals:
            job.duplicate_of_job_post_id = target_canonical.id

    db.execute(update(JobPost).where(JobPost.dedup_group_id == source.id).values(dedup_group_id=target.id))
    db.execute(
        update(TelegramMessage).where(TelegramMessage.dedup_group_id == source.id).values(dedup_group_id=target.id)
    )
    db.execute(update(JobDedupKey).where(JobDedupKey.group_id == source.id).values(group_id=target.id))
    db.delete(source)
    db.flush()


def resolve_group(
    db,
    keys: list[DedupKey],
    *,
    preferred: JobDedupGroup | None = None,
    status: str = "new",
    note: str | None = None,
) -> tuple[JobDedupGroup, bool]:
    """找到或创建长期岗位组，并在多种键相交时自动合并组。"""
    groups = matching_groups(db, keys)
    if preferred is not None and all(group.id != preferred.id for group in groups):
        groups.append(preferred)

    created = not groups
    if created:
        target = JobDedupGroup(status=status, note=note)
        db.add(target)
        db.flush()
    else:
        target = max(
            groups,
            key=lambda group: (
                _status_priority(group.status),
                int(preferred is not None and group.id == preferred.id),
                -group.id,
            ),
        )
        for group in list(groups):
            if group.id != target.id:
                _merge_groups(db, target, group)

    if _status_priority(status) > _status_priority(target.status):
        target.status = status
        target.note = note
    elif note and not target.note:
        target.note = note

    existing = {
        (row.key_type, row.key_hash)
        for row in db.scalars(select(JobDedupKey).where(JobDedupKey.group_id == target.id))
    }
    for key_type, key_hash in keys:
        if (key_type, key_hash) not in existing:
            db.add(JobDedupKey(group_id=target.id, key_type=key_type, key_hash=key_hash))
            existing.add((key_type, key_hash))
    db.flush()
    return target, created


def record_occurrence(group: JobDedupGroup, message: TelegramMessage) -> None:
    if message.dedup_group_id != group.id:
        group.occurrence_count += 1
    message.dedup_group_id = group.id
    group.last_seen_at = datetime.now(timezone.utc)


def canonical_job(db, group_id: int, *, excluding_job_id: int | None = None) -> JobPost | None:
    query = select(JobPost).where(
        JobPost.dedup_group_id == group_id,
        JobPost.duplicate_of_job_post_id.is_(None),
    )
    if excluding_job_id is not None:
        query = query.where(JobPost.id != excluding_job_id)
    return db.scalar(query.order_by(JobPost.id))


def clear_automatic_groups(db) -> int:
    """重建前只清理没有人工决定的空组；忽略、保留和已联系组永久保留。"""
    automatic_ids = list(
        db.scalars(
            select(JobDedupGroup.id).where(
                JobDedupGroup.status == "new",
                ~JobDedupGroup.id.in_(select(JobPost.dedup_group_id).where(JobPost.dedup_group_id.is_not(None))),
            )
        )
    )
    if not automatic_ids:
        return 0
    db.execute(delete(JobDedupKey).where(JobDedupKey.group_id.in_(automatic_ids)))
    db.execute(delete(JobDedupGroup).where(JobDedupGroup.id.in_(automatic_ids)))
    return len(automatic_ids)
