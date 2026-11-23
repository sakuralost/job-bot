from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import or_, select

from app.classifier.llm_client import LLMInfrastructureError, classify
from app.classifier.product_category import is_app_product_role
from app.classifier.schemas import JobClassificationResult
from app.config import settings
from app.database.models import JobPost, WebJobListing
from app.database.session import SessionLocal
from app.dedup.fingerprints import job_dedup_keys, job_fingerprint
from app.dedup.service import canonical_job, resolve_group
from app.services.preference_feedback import ignored_reason_context
from app.services.prompt_service import get_active_prompt
from app.services.source_policy import is_excluded_company
from app.services.web_source_management import WEB_SOURCE_HISTORY_DAYS, is_web_source_enabled, record_web_source_check
from app.services.worker_lock import llm_classification_lock
from worker.classify_pending import _chinese_role_match_reason, _link_contacts, _merge_contacts

SOURCE = "web3.career"
API_URL = "https://web3.career/api/v1"
MAX_RETRIES = 5
WEB_LANGUAGE_APPENDIX = """

# 网站职位语言友好度补充规则
无论原提示词是否包含，下列字段必须出现在 JSON 中：chinese_friendly_level、chinese_friendly_reason。
只依据职位原文判断。明确中文、普通话、华语或中文工作环境为 explicit_chinese；明确没有英语要求或明确中文即可工作为 no_english_requirement；明确英文流利、英文工作语言、商务英语、母语级英语或英文读写听说为 english_required，并设置 english_speaking_required=true；其他为 needs_confirmation。职位原文整体为英文且未明确接受中文办公时，按主提示词设置 is_excluded=true，不要改判为可收录。地点或远程不能单独当作语言证据。
"""


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _is_product_title(value: str) -> bool:
    return bool(re.search(
        r"\b(product manager|product owner|product lead)\b|产品(?:经理|负责人|总监|owner)", value or "", re.I))


def _published_at(item: dict[str, Any]) -> datetime | None:
    epoch = item.get("date_epoch")
    if isinstance(epoch, (int, float)):
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    value = item.get("published_at") or item.get("publishedAt") or item.get("first_published") or item.get("date")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _records(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        values = payload.get("jobs") or payload.get("data") or []
        return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
    if isinstance(payload, list):
        for value in reversed(payload):
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [item for item in payload if isinstance(item, dict)]
    return []


def fetch_jobs(token: str) -> list[dict[str, Any]]:
    query = urlencode({"token": token, "remote": "true", "limit": "100"})
    request = Request(f"{API_URL}?{query}", headers={"User-Agent": "job-bot/0.2 (+https://job.sakuralost.com)"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL is a fixed official endpoint.
        payload = json.loads(response.read().decode("utf-8"))
    return _records(payload)


def _job_text(listing: WebJobListing) -> str:
    tags = ", ".join(listing.tags or [])
    return "\n".join(value for value in (
        f"来源：{listing.source}",
        f"公司：{listing.company_name}" if listing.company_name else "",
        f"岗位：{listing.title}",
        f"地点：{listing.location}" if listing.location else "",
        "平台标记：远程" if listing.is_remote else "",
        f"标签：{tags}" if tags else "",
        f"投递链接：{listing.apply_url}" if listing.apply_url else "",
        "职位描述：",
        listing.description,
    ) if value)


def _language_decision(job_text: str) -> tuple[str, str, bool]:
    normalized = job_text.lower()
    if re.search(r"\b(?:no|not)\s+english\s+(?:required|requirement)\b|无需英语|不要求英语", normalized):
        return "no_english_requirement", "职位原文明确表示无需英语。", False
    if re.search(r"(?:中文|普通话|华语|mandarin|chinese).{0,20}(?:沟通|交流|工作|环境|能力|语言|优先|即可|流利|speaker|proficiency)", job_text, re.I):
        return "explicit_chinese", "职位原文明确提到中文或普通话工作能力。", False
    if re.search(r"(?:fluent|native|business|professional|advanced|strong)\s+english|english.{0,36}(?:fluency|proficien|read(?:ing)?|writ(?:ing)?|speak(?:ing)?|spoken)|英文.{0,20}(?:流利|读写听说)", job_text, re.I):
        return "english_required", "职位原文明确要求英语能力。", True
    return "needs_confirmation", "职位原文未明确中文环境、无需英语或英语硬性要求，需确认。", False


def _normalize_language_result(result: JobClassificationResult, job_text: str) -> JobClassificationResult:
    level, reason, english_required = _language_decision(job_text)
    return result.model_copy(update={
        "chinese_friendly_level": level,
        "chinese_friendly_reason": reason,
        "english_speaking_required": english_required,
    })


def collect_web3_career() -> dict[str, int]:
    if not is_web_source_enabled(SOURCE):
        return {"disabled": 1, "collected": 0, "updated": 0, "excluded_company": 0}
    token = settings().web3_career_api_token.strip()
    if not token:
        return {"skipped_unconfigured": 1, "collected": 0}
    now = datetime.now(timezone.utc)
    all_items = fetch_jobs(token)
    cutoff = now - timedelta(days=WEB_SOURCE_HISTORY_DAYS)
    items = [item for item in all_items if (published_at := _published_at(item)) is not None and published_at >= cutoff]
    out_of_window = len(all_items) - len(items)
    collected = updated = excluded_company = 0
    with SessionLocal() as db:
        for item in items:
            external_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not external_id or not title:
                continue
            company_name = str(item.get("company") or "").strip() or None
            if is_excluded_company(company_name):
                excluded_company += 1
                continue
            apply_url = str(item.get("apply_url") or "").strip() or None
            source_url = str(item.get("url") or apply_url or "https://web3.career/").strip()
            row = db.scalar(select(WebJobListing).where(WebJobListing.source == SOURCE, WebJobListing.external_id == external_id))
            values = {
                "title": title,
                "company_name": company_name,
                "location": str(item.get("location") or item.get("city") or "").strip() or None,
                "is_remote": bool(item.get("is_remote")) if item.get("is_remote") is not None else None,
                "tags": [str(tag) for tag in item.get("tags", []) if str(tag).strip()] if isinstance(item.get("tags"), list) else [],
                "description": _strip_html(str(item.get("description") or "")),
                "raw_payload": item,
                "source_url": source_url,
                "apply_url": apply_url,
                "published_at": _published_at(item),
                "fetched_at": now,
            }
            if row is None:
                status = "pending_classification" if _is_product_title(title) else "collected"
                db.add(WebJobListing(source=SOURCE, external_id=external_id, status=status, **values))
                collected += 1
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                updated += 1
        db.commit()
    record_web_source_check(SOURCE)
    return {"collected": collected, "updated": updated, "excluded_company": excluded_company, "out_of_window": out_of_window}


def _record_occurrence(group, listing: WebJobListing) -> None:
    if listing.dedup_group_id != group.id:
        group.occurrence_count += 1
    listing.dedup_group_id = group.id
    group.last_seen_at = datetime.now(timezone.utc)


def _apply_result(db, listing: WebJobListing, result: JobClassificationResult, *, model_name: str, prompt_version: int) -> str:
    listing.classification_model = model_name
    listing.classification_prompt_version = prompt_version
    listing.classified_at = datetime.now(timezone.utc)
    listing.classification_result_json = result.model_dump(mode="json")
    listing.last_error = None
    common = dict(
        source_web_listing_id=listing.id, job_fingerprint=job_fingerprint(result.company_name, result.job_title, None, result.job_description),
        job_title=result.job_title, company_name=result.company_name, industry=result.industry, work_mode=result.work_mode,
        location=result.location, salary=result.salary, language_requirement=result.language_requirement,
        english_speaking_required=result.english_speaking_required, chinese_friendly_level=result.chinese_friendly_level,
        chinese_friendly_reason=result.chinese_friendly_reason, experience_requirement=result.experience_requirement,
        job_description=result.job_description, job_summary=result.job_summary, role_match_reason=_chinese_role_match_reason(result),
        is_product_role=result.is_product_role, is_app_product_role=is_app_product_role(job_title=result.job_title, job_summary=result.job_summary, job_description=result.job_description, message_text=_job_text(listing)),
        is_remote=result.is_remote, confidence=result.confidence, source_message_link=listing.source_url,
    )
    if not result.is_job_post or not result.is_product_role:
        listing.status = "not_product_role"
        return "not_product_role"
    if result.is_excluded or result.is_remote is False:
        listing.status = "excluded"
        db.add(JobPost(**common, is_excluded=True, excluded_reason=result.excluded_reason or result.excluded_category or "非远程岗位", status="excluded"))
        return "excluded"
    contacts = _merge_contacts(result.contacts, _job_text(listing))
    keys = job_dedup_keys(message_text=_job_text(listing), company=result.company_name, title=result.job_title, contacts=[contact.value for contact in contacts], description=result.job_description)
    group, _ = resolve_group(db, keys)
    _record_occurrence(group, listing)
    if canonical_job(db, group.id) is not None:
        listing.status = "duplicate"
        return "duplicate"
    job = JobPost(**common, is_excluded=False, status=group.status, dedup_group_id=group.id)
    db.add(job)
    db.flush()
    _link_contacts(db, job, contacts)
    listing.status = "classified"
    return "accepted"


def _classify_pending_web_listings_unlocked(
    limit: int = 100,
    *,
    source_prefixes: tuple[str, ...] | None = None,
    statuses: tuple[str, ...] = ("pending_classification",),
    published_after: datetime | None = None,
) -> dict[str, int]:
    with SessionLocal() as db:
        query = select(WebJobListing.id).where(WebJobListing.status.in_(statuses))
        if source_prefixes:
            query = query.where(or_(*(WebJobListing.source.like(f"{prefix}%") for prefix in source_prefixes)))
        if published_after is not None:
            query = query.where(WebJobListing.published_at >= published_after)
        ids = list(db.scalars(query.order_by(WebJobListing.published_at.desc()).limit(max(1, min(limit, 100)))))
        counts: dict[str, int] = {"accepted": 0, "excluded": 0, "duplicate": 0, "not_product_role": 0, "error": 0}
        for listing_id in ids:
            with SessionLocal() as db:
                listing = db.get(WebJobListing, listing_id)
                if listing is None or listing.status not in statuses:
                    continue
                try:
                    prompt = get_active_prompt()
                    job_text = _job_text(listing)
                    result = _normalize_language_result(classify(job_text, system_prompt=prompt.system_prompt + ignored_reason_context(db) + WEB_LANGUAGE_APPENDIX), job_text)
                    outcome = _apply_result(db, listing, result, model_name=settings().openai_model, prompt_version=prompt.version)
                    counts[outcome] = counts.get(outcome, 0) + 1
                    db.commit()
                except LLMInfrastructureError:
                    db.rollback()
                    counts["llm_unavailable"] = 1
                    break
                except Exception as exc:  # noqa: BLE001 - 单条网站职位失败不应阻断其他职位。
                    db.rollback()
                    row = db.get(WebJobListing, listing_id)
                    if row is not None:
                        row.retry_count += 1
                        row.last_error = str(exc)[:2000]
                        if row.retry_count >= MAX_RETRIES:
                            row.status = "error"
                        db.commit()
                    counts["error"] += 1
    return counts


def classify_pending_web_listings(
    limit: int = 100, *, source_prefixes: tuple[str, ...] | None = None
) -> dict[str, int]:
    with llm_classification_lock(blocking=False) as acquired:
        if not acquired:
            return {"skipped_busy": 1}
        return _classify_pending_web_listings_unlocked(limit=limit, source_prefixes=source_prefixes)


def classify_pending_web3_career(limit: int = 100) -> dict[str, int]:
    return classify_pending_web_listings(limit=limit, source_prefixes=(SOURCE,))


def classify_recent_web3_career(days: int = 3, limit: int = 100) -> dict[str, int]:
    """仅用于新来源首次回填；3 天窗口由调用方明确指定，绝不作为常驻任务。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with llm_classification_lock(blocking=False) as acquired:
        if not acquired:
            return {"skipped_busy": 1}
        return _classify_pending_web_listings_unlocked(
            limit=limit,
            source_prefixes=(SOURCE,),
            statuses=("collected", "pending_classification"),
            published_after=cutoff,
        )


def sync_web3_career(limit: int = 100) -> dict[str, int]:
    """常驻同步只收集，不单独抢占 LLM；分类由 Telegram 主队列统一执行。"""
    return collect_web3_career()


if __name__ == "__main__":
    print(sync_web3_career())
