from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.classifier.llm_client import classify
from app.classifier.product_category import is_app_product_role
from app.classifier.schemas import JobClassificationResult
from app.config import settings
from app.database.models import (
    JobContact,
    JobDedupGroup,
    JobPost,
    ReclassificationItem,
    ReclassificationRun,
    TelegramMessage,
)
from app.database.session import SessionLocal
from app.dedup.fingerprints import job_dedup_keys, job_fingerprint
from app.dedup.service import MANUAL_STATUSES, canonical_job, record_occurrence, resolve_group
from app.services.prompt_service import PromptSnapshot, get_active_prompt
from app.services.worker_lock import llm_classification_lock
from worker.backfill_dedup import backfill_existing_jobs
from worker.classify_pending import _chinese_role_match_reason, _link_contacts, _merge_contacts

MAX_ITEM_ATTEMPTS = 3


def create_run(prompt: PromptSnapshot) -> ReclassificationRun:
    with SessionLocal.begin() as db:
        older_runs = list(
            db.scalars(
                select(ReclassificationRun).where(
                    ReclassificationRun.prompt_version != prompt.version,
                    ReclassificationRun.status.in_(("pending", "running")),
                )
            )
        )
        for older_run in older_runs:
            older_run.status = "superseded"
        existing = db.scalar(
            select(ReclassificationRun)
            .where(
                ReclassificationRun.prompt_version == prompt.version,
                ReclassificationRun.status.in_(("pending", "running")),
            )
            .order_by(ReclassificationRun.id.desc())
        )
        if existing is not None:
            return existing

        job_rows = list(
            db.execute(
                select(JobPost.id, JobPost.source_message_id).where(
                    JobPost.dedup_group_id.is_not(None),
                    JobPost.duplicate_of_job_post_id.is_(None),
                )
            )
        )
        run = ReclassificationRun(
            prompt_version=prompt.version,
            status="pending",
            total_items=len(job_rows),
        )
        db.add(run)
        db.flush()
        db.add_all(
            ReclassificationItem(
                run_id=run.id,
                message_id=source_message_id,
                job_post_id=job_id,
            )
            for job_id, source_message_id in job_rows
        )
        db.flush()
        db.refresh(run)
        return run


def _set_common_fields(
    job: JobPost,
    message: TelegramMessage,
    result: JobClassificationResult,
) -> None:
    job.job_title = result.job_title
    job.company_name = result.company_name
    job.industry = result.industry
    job.work_mode = result.work_mode
    job.location = result.location
    job.salary = result.salary
    job.language_requirement = result.language_requirement
    job.experience_requirement = result.experience_requirement
    job.job_description = result.job_description
    job.job_summary = result.job_summary
    job.role_match_reason = _chinese_role_match_reason(result)
    job.is_product_role = result.is_product_role
    job.is_app_product_role = is_app_product_role(
        job_title=result.job_title,
        job_summary=result.job_summary,
        job_description=result.job_description,
        message_text=message.message_text,
    )
    job.is_remote = result.is_remote
    job.confidence = result.confidence


def _apply_reclassification(
    db,
    job: JobPost,
    message: TelegramMessage,
    result: JobClassificationResult,
    *,
    prompt: PromptSnapshot,
    model_name: str,
) -> str:
    current_group = db.get(JobDedupGroup, job.dedup_group_id) if job.dedup_group_id else None
    manual_status = (
        current_group.status
        if current_group is not None and current_group.status in MANUAL_STATUSES
        else job.status if job.status in MANUAL_STATUSES else None
    )
    message.classification_model = model_name
    message.classification_prompt_version = prompt.version
    message.classified_at = datetime.now(timezone.utc)
    message.last_error = None
    _set_common_fields(job, message, result)

    if not result.is_job_post or not result.is_product_role:
        job.excluded_reason = "重新筛选后不属于产品经理岗位"
        job.is_excluded = manual_status is None
        job.status = manual_status or "excluded"
        message.status = "not_product_role"
        return "excluded"
    if result.is_excluded or result.is_remote is False:
        job.excluded_reason = (
            result.excluded_reason
            or result.excluded_category
            or ("非远程岗位" if result.is_remote is False else "命中提示词排除条件")
        )
        job.is_excluded = manual_status is None
        job.status = manual_status or "excluded"
        message.status = "excluded"
        return "excluded"

    contacts = _merge_contacts(result.contacts, message.message_text)
    primary_contact = next((item.value for item in contacts if item.is_primary), contacts[0].value if contacts else None)
    job.job_fingerprint = job_fingerprint(
        result.company_name,
        result.job_title,
        primary_contact,
        result.job_description,
    )
    keys = job_dedup_keys(
        message_text=message.message_text,
        company=result.company_name,
        title=result.job_title,
        contacts=[item.value for item in contacts],
        description=result.job_description,
    )
    preferred = db.get(JobDedupGroup, job.dedup_group_id) if job.dedup_group_id else None
    group, _ = resolve_group(db, keys, preferred=preferred)
    record_occurrence(group, message)
    job.dedup_group_id = group.id

    existing = canonical_job(db, group.id, excluding_job_id=job.id)
    if existing is not None:
        job.duplicate_of_job_post_id = existing.id
        job.is_excluded = False
        job.excluded_reason = None
        job.status = group.status
        message.status = "duplicate"
        return "duplicate"

    job.duplicate_of_job_post_id = None
    job.is_excluded = False
    job.excluded_reason = None
    job.status = group.status
    db.execute(delete(JobContact).where(JobContact.job_post_id == job.id))
    _link_contacts(db, job, contacts)
    message.status = "classified"
    return "accepted"


def run_reclassification(run_id: int, prompt: PromptSnapshot) -> dict[str, int]:
    model_name = settings().openai_model
    with SessionLocal.begin() as db:
        run = db.get(ReclassificationRun, run_id)
        if run is None:
            raise LookupError(f"未找到重新筛选任务 #{run_id}")
        if run.prompt_version != prompt.version:
            raise RuntimeError(
                f"任务锁定提示词 v{run.prompt_version}，但当前启用的是 v{prompt.version}；为避免混用规则，任务已停止"
            )
        run.status = "running"
        pending_ids = list(
            db.scalars(
                select(ReclassificationItem.id)
                .where(
                    ReclassificationItem.run_id == run_id,
                    ReclassificationItem.status == "pending",
                )
                .order_by(ReclassificationItem.id)
            )
        )

    print(
        f"历史岗位重新筛选开始：任务 #{run_id}，提示词 v{prompt.version}，待处理 {len(pending_ids)} 条",
        flush=True,
    )
    for index, item_id in enumerate(pending_ids, start=1):
        try:
            with SessionLocal.begin() as db:
                item = db.get(ReclassificationItem, item_id)
                if item is None or item.status != "pending":
                    continue
                job = db.get(JobPost, item.job_post_id)
                message = db.get(TelegramMessage, item.message_id)
                if job is None or message is None:
                    raise LookupError("原岗位或原消息已不存在")
                result = classify(message.message_text, system_prompt=prompt.system_prompt)
                outcome = _apply_reclassification(
                    db,
                    job,
                    message,
                    result,
                    prompt=prompt,
                    model_name=model_name,
                )
                item.status = "completed"
                item.outcome = outcome
                item.processed_at = datetime.now(timezone.utc)
                run = db.get(ReclassificationRun, run_id)
                run.processed_items += 1
                if outcome == "accepted":
                    run.accepted_items += 1
                elif outcome == "duplicate":
                    run.duplicate_items += 1
                else:
                    run.excluded_items += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不能中断整批历史任务
            with SessionLocal.begin() as db:
                item = db.get(ReclassificationItem, item_id)
                if item is not None:
                    item.status = "error"
                    item.error_message = str(exc)[:2000]
                    item.processed_at = datetime.now(timezone.utc)
                run = db.get(ReclassificationRun, run_id)
                run.processed_items += 1
                run.error_items += 1
            print(f"[{index}/{len(pending_ids)}] item={item_id} 失败：{exc}", file=sys.stderr, flush=True)
        if index % 10 == 0 or index == len(pending_ids):
            with SessionLocal() as db:
                current = db.get(ReclassificationRun, run_id)
                print(
                    f"重新筛选进度 {current.processed_items}/{current.total_items}："
                    f"保留 {current.accepted_items}，排除 {current.excluded_items}，"
                    f"重复 {current.duplicate_items}，错误 {current.error_items}",
                    flush=True,
                )

    with SessionLocal.begin() as db:
        run = db.get(ReclassificationRun, run_id)
        remaining = db.scalar(
            select(ReclassificationItem.id)
            .where(
                ReclassificationItem.run_id == run_id,
                ReclassificationItem.status == "pending",
            )
            .limit(1)
        )
        run.status = "completed" if remaining is None else "running"
        if run.status == "completed":
            run.finished_at = datetime.now(timezone.utc)
        result = {
            "run_id": run.id,
            "total": run.total_items,
            "processed": run.processed_items,
            "accepted": run.accepted_items,
            "excluded": run.excluded_items,
            "duplicate": run.duplicate_items,
            "error": run.error_items,
        }
    print(f"历史岗位重新筛选结束：{result}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args()
    prompt = get_active_prompt()
    if args.run_id is None:
        print(f"去重初始化：{backfill_existing_jobs()}", flush=True)
        run = create_run(prompt)
        run_id = run.id
    else:
        run_id = args.run_id
    with llm_classification_lock(blocking=True):
        run_reclassification(run_id, prompt)


if __name__ == "__main__":
    main()
