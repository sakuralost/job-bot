from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.database.models import WebJobListing
from app.database.session import SessionLocal
from app.services.source_policy import is_excluded_company
from app.services.web_source_management import WEB_SOURCE_HISTORY_DAYS, is_web_source_enabled, record_web_source_check
from worker.web3_career import _is_product_title, _strip_html


USER_AGENT = "job-bot/0.5 (+https://job.sakuralost.com)"
SOURCES = ("himalayas.api", "remoteok.api", "jobicy.api")


def _get_json(url: str) -> Any:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def _location(value: object) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip() or None


def _himalayas_records() -> list[dict[str, Any]]:
    query = urlencode({"q": "product manager", "sort": "recent", "page": "1"})
    payload = _get_json(f"https://himalayas.app/jobs/api/search?{query}")
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    records: list[dict[str, Any]] = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict):
            continue
        records.append({
            "external_id": str(job.get("guid") or ""),
            "title": str(job.get("title") or "").strip(),
            "company_name": str(job.get("companyName") or "").strip() or None,
            "location": _location(job.get("locationRestrictions")),
            "is_remote": True,
            "tags": [str(tag) for tag in (job.get("parentCategories") or job.get("categories") or [])],
            "description": _strip_html(str(job.get("description") or job.get("excerpt") or "")),
            "source_url": str(job.get("applicationLink") or "https://himalayas.app/"),
            "apply_url": str(job.get("applicationLink") or "").strip() or None,
            "published_at": _datetime(job.get("pubDate")),
            "raw_payload": job,
        })
    return records


def _remoteok_records() -> list[dict[str, Any]]:
    payload = _get_json("https://remoteok.com/api")
    records: list[dict[str, Any]] = []
    for job in payload if isinstance(payload, list) else []:
        if not isinstance(job, dict) or not job.get("position"):
            continue
        records.append({
            "external_id": str(job.get("id") or job.get("slug") or ""),
            "title": str(job.get("position") or "").strip(),
            "company_name": str(job.get("company") or "").strip() or None,
            "location": _location(job.get("location")),
            "is_remote": True,
            "tags": [str(tag) for tag in job.get("tags", []) if str(tag).strip()] if isinstance(job.get("tags"), list) else [],
            "description": _strip_html(str(job.get("description") or "")),
            "source_url": str(job.get("url") or "https://remoteok.com/"),
            "apply_url": str(job.get("apply_url") or job.get("url") or "").strip() or None,
            "published_at": _datetime(job.get("epoch") or job.get("date")),
            "raw_payload": job,
        })
    return records


def _jobicy_records() -> list[dict[str, Any]]:
    payload = _get_json("https://jobicy.com/api/v2/remote-jobs?count=50")
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    records: list[dict[str, Any]] = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict):
            continue
        records.append({
            "external_id": str(job.get("id") or job.get("jobSlug") or ""),
            "title": str(job.get("jobTitle") or "").strip(),
            "company_name": str(job.get("companyName") or "").strip() or None,
            "location": _location(job.get("jobGeo")),
            "is_remote": True,
            "tags": [str(tag) for tag in job.get("jobIndustry", []) if str(tag).strip()] if isinstance(job.get("jobIndustry"), list) else [],
            "description": _strip_html(str(job.get("jobDescription") or job.get("jobExcerpt") or "")),
            "source_url": str(job.get("url") or "https://jobicy.com/"),
            "apply_url": str(job.get("url") or "").strip() or None,
            "published_at": _datetime(job.get("pubDate")),
            "raw_payload": job,
        })
    return records


def _collect(source: str, fetcher) -> dict[str, int]:
    if not is_web_source_enabled(source):
        return {"disabled": 1, "collected": 0, "updated": 0, "excluded_company": 0, "out_of_window": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WEB_SOURCE_HISTORY_DAYS)
    totals = {"collected": 0, "updated": 0, "excluded_company": 0, "out_of_window": 0}
    try:
        records = fetcher()
        with SessionLocal() as db:
            for record in records:
                published_at = record["published_at"]
                if not record["external_id"] or not record["title"] or published_at is None or published_at < cutoff:
                    totals["out_of_window"] += 1
                    continue
                if not _is_product_title(record["title"]):
                    continue
                if is_excluded_company(record["company_name"]):
                    totals["excluded_company"] += 1
                    continue
                row = db.scalar(select(WebJobListing).where(WebJobListing.source == source, WebJobListing.external_id == record["external_id"]))
                values = {**record, "fetched_at": now}
                if row is None:
                    db.add(WebJobListing(source=source, status="pending_classification", **values))
                    totals["collected"] += 1
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    if row.status == "collected":
                        row.status = "pending_classification"
                    totals["updated"] += 1
            db.commit()
    except Exception as exc:
        record_web_source_check(source, str(exc))
        raise
    record_web_source_check(source)
    return totals


def collect_public_remote_boards() -> dict[str, dict[str, int]]:
    return {
        "himalayas.api": _collect("himalayas.api", _himalayas_records),
        "remoteok.api": _collect("remoteok.api", _remoteok_records),
        "jobicy.api": _collect("jobicy.api", _jobicy_records),
    }
