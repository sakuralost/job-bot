from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.classifier.llm_client import classify
from app.classifier.schemas import ExtractedContact, JobClassificationResult
from app.database.models import Contact, JobContact, JobPost, TelegramMessage
from app.database.session import SessionLocal
from app.dedup.fingerprints import job_fingerprint
from app.extractor import contacts as contact_extractor
from app.extractor.normalizer import email as norm_email
from app.extractor.normalizer import phone as norm_phone
from app.extractor.normalizer import telegram_username as norm_username
from app.extractor.normalizer import wechat as norm_wechat

MAX_RETRIES = 5


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


def _apply_result(db, message: TelegramMessage, result: JobClassificationResult) -> str:
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
            role_match_reason=result.role_match_reason, is_product_role=result.is_product_role,
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
            role_match_reason=result.role_match_reason, is_product_role=result.is_product_role,
            is_remote=result.is_remote, is_excluded=True, excluded_reason="非远程岗位",
            confidence=result.confidence, source_message_link=None, status="excluded",
        )
        db.add(job)
        return "excluded_not_remote"

    contacts = _merge_contacts(result.contacts, message.message_text)
    primary_contact = next((c.value for c in contacts if c.is_primary), contacts[0].value if contacts else None)
    fingerprint = job_fingerprint(result.company_name, result.job_title, primary_contact, result.job_description)

    duplicate = db.scalar(select(JobPost).where(JobPost.job_fingerprint == fingerprint, JobPost.is_excluded.is_(False)))
    if duplicate is not None:
        message.status = "duplicate"
        return "duplicate"

    job = JobPost(
        source_message_id=message.id, job_fingerprint=fingerprint,
        job_title=result.job_title, company_name=result.company_name, industry=result.industry,
        work_mode=result.work_mode, location=result.location, salary=result.salary,
        language_requirement=result.language_requirement, experience_requirement=result.experience_requirement,
        job_description=result.job_description, job_summary=result.job_summary,
        role_match_reason=result.role_match_reason, is_product_role=result.is_product_role,
        is_remote=result.is_remote, is_excluded=False, confidence=result.confidence,
        source_message_link=None, status="new",
    )
    db.add(job)
    db.flush()
    _link_contacts(db, job, contacts)
    message.status = "classified"
    return "accepted"


def classify_pending() -> dict[str, int]:
    counts = {"accepted": 0, "excluded": 0, "excluded_not_remote": 0, "duplicate": 0, "not_product_role": 0, "error": 0, "skipped_unconfigured": 0}
    with SessionLocal() as db:
        pending_ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.status == "pending_classification")))
    total = len(pending_ids)
    print(f"LLM 待分类消息 {total} 条", flush=True)
    for index, message_id in enumerate(pending_ids, start=1):
        with SessionLocal() as db:
            message = db.get(TelegramMessage, message_id)
            if message is None or message.status != "pending_classification":
                continue
            try:
                result = classify(message.message_text)
                outcome = _apply_result(db, message, result)
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
    print(f"LLM 分类完成：{counts}", flush=True)
    return counts


if __name__ == "__main__":
    classify_pending()
