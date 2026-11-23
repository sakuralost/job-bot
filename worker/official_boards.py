from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.database.models import WebJobListing
from app.database.session import SessionLocal
from app.services.web_source_management import WEB_SOURCE_HISTORY_DAYS, is_web_source_enabled, record_web_source_check
from worker.web3_career import _is_product_title, _published_at, _strip_html, classify_pending_web_listings


CONFIG_PATH = Path("/app/config/company_job_boards.json")
USER_AGENT = "job-bot/0.3 (+https://job.sakuralost.com)"


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URLs are built from local source configuration.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("公开职位接口未返回 JSON 对象")
    return payload


def _get_json_list(url: str) -> list[dict[str, Any]]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URLs are built from local source configuration.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("公开职位接口未返回 JSON 数组")
    return [job for job in payload if isinstance(job, dict)]


def _tags(*values: object) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
        elif isinstance(value, list):
            result.extend(_tags(*value))
        elif isinstance(value, dict):
            result.extend(_tags(value.get("name"), value.get("label")))
    return list(dict.fromkeys(result))


def _remote_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.lower()
    if any(word in normalized for word in ("remote", "distributed", "anywhere", "work from home")):
        return True
    return None


def _greenhouse_records(board: str, company: str) -> list[dict[str, Any]]:
    payload = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    records: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id") or not job.get("title"):
            continue
        location = job.get("location") if isinstance(job.get("location"), dict) else {}
        location_name = str(location.get("name") or "").strip()
        records.append({
            "external_id": str(job["id"]),
            "title": str(job["title"]).strip(),
            "company_name": str(job.get("company_name") or company).strip() or company,
            "location": location_name or None,
            "is_remote": _remote_value(location_name),
            "tags": _tags(job.get("departments"), job.get("offices"), job.get("employment"), job.get("language")),
            "description": _strip_html(str(job.get("content") or "")),
            "source_url": str(job.get("absolute_url") or f"https://boards.greenhouse.io/{board}").strip(),
            "apply_url": str(job.get("absolute_url") or "").strip() or None,
            "published_at": _published_at({"publishedAt": job.get("first_published") or job.get("updated_at")}),
            "raw_payload": job,
        })
    return records


def _ashby_records(board: str, company: str) -> list[dict[str, Any]]:
    payload = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true")
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    records: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id") or not job.get("title") or job.get("isListed") is False:
            continue
        location = str(job.get("location") or "").strip()
        records.append({
            "external_id": str(job["id"]),
            "title": str(job["title"]).strip(),
            "company_name": company,
            "location": location or None,
            "is_remote": _remote_value(job.get("isRemote")) or _remote_value(location),
            "tags": _tags(job.get("department"), job.get("team"), job.get("employmentType"), job.get("workplaceType")),
            "description": str(job.get("descriptionPlain") or _strip_html(str(job.get("descriptionHtml") or ""))).strip(),
            "source_url": str(job.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}").strip(),
            "apply_url": str(job.get("applyUrl") or job.get("jobUrl") or "").strip() or None,
            "published_at": _published_at({"publishedAt": job.get("publishedAt")}),
            "raw_payload": job,
        })
    return records


def _lever_records(board: str, company: str) -> list[dict[str, Any]]:
    """读取 Lever 公开职位接口；该接口是招聘页自身的结构化数据源。"""
    records: list[dict[str, Any]] = []
    limit = 20
    for skip in range(0, 20, limit):
        jobs = _get_json_list(f"https://api.lever.co/v0/postings/{board}?mode=json&skip={skip}&limit={limit}")
        for job in jobs:
            if not job.get("id") or not job.get("text"):
                continue
            categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
            location = str(categories.get("location") or "").strip()
            created_at = job.get("createdAt")
            published_at = (
                datetime.fromtimestamp(created_at / 1_000, tz=timezone.utc)
                if isinstance(created_at, (int, float))
                else None
            )
            records.append({
                "external_id": str(job["id"]),
                "title": str(job["text"]).strip(),
                "company_name": company,
                "location": location or None,
                "is_remote": _remote_value(job.get("workplaceType")) or _remote_value(location),
                "tags": _tags(categories, job.get("lists"), job.get("workplaceType")),
                "description": str(job.get("descriptionPlain") or job.get("descriptionBodyPlain") or _strip_html(str(job.get("description") or ""))).strip(),
                "source_url": str(job.get("hostedUrl") or f"https://jobs.lever.co/{board}").strip(),
                "apply_url": str(job.get("applyUrl") or job.get("hostedUrl") or "").strip() or None,
                "published_at": published_at,
                "raw_payload": job,
            })
        if len(jobs) < limit:
            break
    return records


def _load_sources(config_path: Path = CONFIG_PATH) -> list[dict[str, str]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    return [source for source in sources if isinstance(source, dict) and source.get("enabled", True)]


def collect_official_boards(config_path: Path = CONFIG_PATH) -> dict[str, int]:
    totals = {"collected": 0, "updated": 0, "failed_sources": 0, "out_of_window": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WEB_SOURCE_HISTORY_DAYS)
    with SessionLocal() as db:
        for source in _load_sources(config_path):
            provider = str(source.get("provider") or "").strip().lower()
            board = str(source.get("board") or "").strip()
            company = str(source.get("company") or board).strip()
            if provider not in {"greenhouse", "ashby", "lever"} or not board:
                totals["failed_sources"] += 1
                continue
            source_key = f"{provider}:{board}"
            if not is_web_source_enabled(source_key):
                continue
            try:
                if provider == "greenhouse":
                    records = _greenhouse_records(board, company)
                elif provider == "ashby":
                    records = _ashby_records(board, company)
                else:
                    records = _lever_records(board, company)
            except Exception:  # noqa: BLE001 - one public board must not stop the remaining boards.
                totals["failed_sources"] += 1
                record_web_source_check(source_key, "官网公开职位接口读取失败")
                continue
            for record in records:
                published_at = record.get("published_at")
                if published_at is None or published_at < cutoff:
                    totals["out_of_window"] += 1
                    continue
                row = db.scalar(select(WebJobListing).where(WebJobListing.source == source_key, WebJobListing.external_id == record["external_id"]))
                values = {**record, "fetched_at": now}
                if row is None:
                    status = "pending_classification" if _is_product_title(record["title"]) else "collected"
                    db.add(WebJobListing(source=source_key, status=status, **values))
                    totals["collected"] += 1
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    if row.status == "collected" and _is_product_title(row.title):
                        row.status = "pending_classification"
                    totals["updated"] += 1
            record_web_source_check(source_key)
        db.commit()
    return totals


def sync_official_boards(limit: int = 100) -> dict[str, int]:
    """常驻同步只收集，不单独抢占 LLM；分类由 Telegram 主队列统一执行。"""
    return collect_official_boards()


if __name__ == "__main__":
    print(sync_official_boards())
