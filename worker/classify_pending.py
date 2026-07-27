from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from app.classifier.llm_client import classify
from app.classifier.product_category import is_app_product_role
from app.classifier.schemas import ExtractedContact, JobClassificationResult
from app.config import settings
from app.database.models import Contact, JobContact, JobPost, TelegramMessage
from app.database.session import SessionLocal
from app.dedup.fingerprints import job_dedup_keys, job_fingerprint
from app.dedup.service import canonical_job, record_occurrence, resolve_group
from app.extractor import contacts as contact_extractor
from app.extractor.normalizer import email as norm_email
from app.extractor.normalizer import phone as norm_phone
from app.extractor.normalizer import telegram_username as norm_username
from app.extractor.normalizer import wechat as norm_wechat
from app.services.prompt_service import get_active_prompt
from app.services.preference_feedback import ignored_reason_context
from app.services.worker_lock import llm_classification_lock

MAX_RETRIES = 5


def _personalized_prompt(db, system_prompt: str) -> str:
    return system_prompt + ignored_reason_context(db)


def _contains_chinese(value: str | None) -> bool:
    return bool(value and any("\u4e00" <= character <= "\u9fff" for character in value))


def _chinese_role_match_reason(result: JobClassificationResult) -> str:
    if _contains_chinese(result.role_match_reason):
        return result.role_match_reason or ""
    work_mode = {
        "remote": "明确支持远程工作",
        "hybrid": "属于混合办公",
        "onsite": "需要现场办公",
        "unknown": "未说明办公方式",
    }.get(result.work_mode, "办公方式未明确")
    parts = ["模型识别为产品经理相关岗位", work_mode]
    if result.location:
        parts.append(f"地点为 {result.location}")
    parts.append(f"匹配度为 {round(result.confidence * 100)}%")
    return "；".join(parts)


def _normalized_identity(contact: ExtractedContact) -> str:
    if contact.contact_type == "telegram_username":
        return f"tg:{norm_username(contact.value)}"
    if contact.contact_type == "email":
        return f"email:{norm_email(contact.value)}"
    if contact.contact_type == "phone":
        return f"phone:{norm_phone(contact.value)}"
    if contact.contact_type == "wechat":
        return f"wechat:{norm_wechat(contact.value)}"
    return f"{contact.contact_type}:{contact.value.strip().lower()}"


def _merge_contacts(llm_contacts: list[ExtractedContact], text: str) -> list[ExtractedContact]:
    """LLM 抽取的联系方式为主，正则抽取补漏；按归一化身份去重。"""
    merged: dict[str, ExtractedContact] = {}
    for contact in [*llm_contacts, *contact_extractor.extract(text)]:
        key = _normalized_identity(contact)
        if key not in merged or contact.is_primary:
            merged[key] = contact
    return list(merged.values())


def _link_contacts(db, job_post: JobPost, contacts: list[ExtractedContact]) -> None:
    now = datetime.now(timezone.utc)
    for contact in contacts:
        identity = _normalized_identity(contact)
        row = db.scalar(select(Contact).where(Contact.normalized_identity == identity))
        if row is None:
            row = Contact(normalized_identity=identity, display_name=contact.value, first_seen_at=now, last_seen_at=now)
            if contact.contact_type == "telegram_username":
                row.telegram_username = norm_username(contact.value)
            elif contact.contact_type == "email":
                row.email = norm_email(contact.value)
            elif contact.contact_type == "phone":
                row.phone = norm_phone(contact.value)
            elif contact.contact_type == "wechat":
                row.wechat = norm_wechat(contact.value)
            elif contact.contact_type == "discord":
                row.discord = contact.value
            db.add(row)
            db.flush()
        else:
            row.last_seen_at = now
        db.add(JobContact(job_post_id=job_post.id, contact_id=row.id, contact_type=contact.contact_type, contact_value=contact.value, is_primary=contact.is_primary))


def _apply_result(
    db,
    message: TelegramMessage,
    result: JobClassificationResult,
    *,
    model_name: str,
    prompt_version: int,
) -> str:
    message.classification_model = model_name
    message.classification_prompt_version = prompt_version
    message.classified_at = datetime.now(timezone.utc)
    message.last_error = None
    app_product_role = is_app_product_role(
        job_title=result.job_title,
        job_summary=result.job_summary,
        job_description=result.job_description,
        message_text=message.message_text,
    )

    if not result.is_job_post or not result.is_product_role:
        message.status = "not_product_role"
        return "not_product_role"

    if result.is_excluded:
        message.status = "excluded"
        job = JobPost(
            source_message_id=message.id,
            job_fingerprint=job_fingerprint(result.company_name, result.job_title, None, result.job_description),
            job_title=result.job_title, company_name=result.company_name, industry=result.industry,
            work_mode=result.work_mode, location=result.location, salary=result.salary,
            language_requirement=result.language_requirement, experience_requirement=result.experience_requirement,
            job_description=result.job_description, job_summary=result.job_summary,
            role_match_reason=_chinese_role_match_reason(result), is_product_role=result.is_product_role,
            is_app_product_role=app_product_role,
            is_remote=result.is_remote, is_excluded=True, excluded_reason=result.excluded_reason or result.excluded_category,
            confidence=result.confidence, source_message_link=None, status="excluded",
        )
        db.add(job)
        return "excluded"

    # 用户明确要求：剔除已确认非远程的岗位（未说明远程与否的暂时保留，避免模型漏判导致误删）
    if result.is_remote is False:
        message.status = "excluded"
        job = JobPost(
            source_message_id=message.id,
            job_fingerprint=job_fingerprint(result.company_name, result.job_title, None, result.job_description),
            job_title=result.job_title, company_name=result.company_name, industry=result.industry,
            work_mode=result.work_mode, location=result.location, salary=result.salary,
            language_requirement=result.language_requirement, experience_requirement=result.experience_requirement,
            job_description=result.job_description, job_summary=result.job_summary,
            role_match_reason=_chinese_role_match_reason(result), is_product_role=result.is_product_role,
            is_app_product_role=app_product_role,
            is_remote=result.is_remote, is_excluded=True, excluded_reason="非远程岗位",
            confidence=result.confidence, source_message_link=None, status="excluded",
        )
        db.add(job)
        return "excluded_not_remote"

    contacts = _merge_contacts(result.contacts, message.message_text)
    primary_contact = next((c.value for c in contacts if c.is_primary), contacts[0].value if contacts else None)
    fingerprint = job_fingerprint(result.company_name, result.job_title, primary_contact, result.job_description)
    keys = job_dedup_keys(
        message_text=message.message_text,
        company=result.company_name,
        title=result.job_title,
        contacts=[contact.value for contact in contacts],
        description=result.job_description,
    )
    group, _ = resolve_group(db, keys)
    record_occurrence(group, message)
    duplicate = canonical_job(db, group.id)
    if duplicate is not None:
        message.status = "duplicate"
        return "duplicate"

    job = JobPost(
        source_message_id=message.id, job_fingerprint=fingerprint,
        job_title=result.job_title, company_name=result.company_name, industry=result.industry,
        work_mode=result.work_mode, location=result.location, salary=result.salary,
        language_requirement=result.language_requirement, experience_requirement=result.experience_requirement,
        job_description=result.job_description, job_summary=result.job_summary,
        role_match_reason=_chinese_role_match_reason(result), is_product_role=result.is_product_role,
        is_app_product_role=app_product_role,
        is_remote=result.is_remote, is_excluded=False, confidence=result.confidence,
        source_message_link=None, status=group.status, dedup_group_id=group.id,
    )
    db.add(job)
    db.flush()
    _link_contacts(db, job, contacts)
    message.status = "classified"
    return "accepted"


def _classify_pending_unlocked(
    progress_callback: Callable[[int, int, dict[str, int]], None] | None = None,
) -> dict[str, int]:
    counts = {"accepted": 0, "excluded": 0, "excluded_not_remote": 0, "duplicate": 0, "not_product_role": 0, "error": 0, "skipped_unconfigured": 0}
    model_name = settings().openai_model
    with SessionLocal() as db:
        pending_ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.status == "pending_classification")))
    total = len(pending_ids)
    print(f"LLM 待分类消息 {total} 条", flush=True)
    last_prompt_version: int | None = None
    for index, message_id in enumerate(pending_ids, start=1):
        with SessionLocal() as db:
            message = db.get(TelegramMessage, message_id)
            if message is None or message.status != "pending_classification":
                continue
            try:
                prompt = get_active_prompt()
                if prompt.version != last_prompt_version:
                    print(f"LLM 使用提示词版本 v{prompt.version}；后续新任务会自动采用后台最新版本", flush=True)
                    last_prompt_version = prompt.version
                personalized_prompt = _personalized_prompt(db, prompt.system_prompt)
                result = classify(message.message_text, system_prompt=personalized_prompt)
                outcome = _apply_result(
                    db,
                    message,
                    result,
                    model_name=model_name,
                    prompt_version=prompt.version,
                )
                counts[outcome] = counts.get(outcome, 0) + 1
                db.commit()
            except RuntimeError as exc:
                # LLM 未配置：消息保留为 pending_classification，不丢失，下次运行重试
                print(f"[{index}/{total}] message={message_id} 跳过：{exc}", file=sys.stderr, flush=True)
                counts["skipped_unconfigured"] += 1
                db.rollback()
                break
            except Exception as exc:  # noqa: BLE001 - 记录后继续处理下一条，不让单条错误中断整批
                db.rollback()
                with SessionLocal() as db2:
                    row = db2.get(TelegramMessage, message_id)
                    row.retry_count += 1
                    row.last_error = str(exc)[:2000]
                    if row.retry_count >= MAX_RETRIES:
                        row.status = "error"
                    db2.commit()
                print(f"[{index}/{total}] message={message_id} error={exc}", file=sys.stderr, flush=True)
                counts["error"] += 1
        if index % 10 == 0 or index == total:
            print(f"LLM 进度 {index}/{total}：{counts}", flush=True)
        if progress_callback is not None:
            progress_callback(index, total, counts)
    print(f"LLM 分类完成：{counts}", flush=True)
    return counts


def classify_pending() -> dict[str, int]:
    with llm_classification_lock(blocking=False) as acquired:
        if not acquired:
            print("历史重新筛选正在使用本地模型；本轮新增消息复核延后到下次任务", flush=True)
            return {"skipped_busy": 1}
        return _classify_pending_unlocked()


if __name__ == "__main__":
    classify_pending()
