from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from sqlalchemy import select

from app.database.models import WebJobListing
from app.database.session import SessionLocal
from app.services.source_policy import is_excluded_company
from app.services.web_source_management import WEB_SOURCE_HISTORY_DAYS, is_web_source_enabled, record_web_source_check
from worker.web3_career import _is_product_title, _strip_html


SOURCE = "remote3.rss"
RSS_URL = "https://remote3.co/api/rss"
USER_AGENT = "job-bot/0.4 (+https://job.sakuralost.com)"
MAX_ITEMS_PER_SYNC = 80


def _rss_items(payload: bytes) -> list[dict[str, str]]:
    root = ElementTree.fromstring(payload)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        values = {
            key: (item.findtext(key) or "").strip()
            for key in ("title", "link", "guid", "pubDate", "description")
        }
        if values["title"] and values["link"]:
            items.append(values)
    return items


def _published_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _company_and_location(summary: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"\bat\s+(.+?)\s+-\s+(?:full-time|part-time|contract|internship)\s+-\s+(.+)$",
        summary,
        re.I,
    )
    if match is None:
        return None, None
    return match.group(1).strip() or None, match.group(2).strip() or None


def _remote_value(location: str | None) -> bool | None:
    if not location:
        return None
    return True if any(word in location.lower() for word in ("remote", "worldwide", "anywhere")) else None


def _detail_description(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL comes from the source RSS feed.
        page = response.read().decode("utf-8", errors="replace")
    marker = page.find("RemoteJobs_jobDescription")
    if marker < 0:
        return ""
    fragment = page[marker : marker + 80_000]
    fragment = fragment.split("RemoteJobs_actionsContainer", 1)[0]
    return _strip_html(fragment)


def collect_remote3() -> dict[str, int]:
    if not is_web_source_enabled(SOURCE):
        return {"disabled": 1, "collected": 0, "updated": 0, "excluded_company": 0, "failed_items": 0}
    request = Request(RSS_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - public RSS feed published by Remote3.
        items = _rss_items(response.read())

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WEB_SOURCE_HISTORY_DAYS)
    totals = {"collected": 0, "updated": 0, "excluded_company": 0, "failed_items": 0, "out_of_window": 0}
    with SessionLocal() as db:
        for item in items[:MAX_ITEMS_PER_SYNC]:
            published_at = _published_at(item["pubDate"])
            if published_at is None or published_at < cutoff:
                totals["out_of_window"] += 1
                continue
            title = item["title"]
            if not _is_product_title(title):
                continue
            company, location = _company_and_location(item["description"])
            if is_excluded_company(company):
                totals["excluded_company"] += 1
                continue
            link = item["link"]
            external_id = item["guid"] or urlparse(link).path.rstrip("/")
            if not external_id:
                totals["failed_items"] += 1
                continue
            try:
                description = _detail_description(link) or item["description"]
            except Exception:  # noqa: BLE001 - a single RSS detail page must not block the feed.
                totals["failed_items"] += 1
                continue
            record: dict[str, Any] = {
                "title": title,
                "company_name": company,
                "location": location,
                "is_remote": _remote_value(location),
                "tags": ["remote3", "rss"],
                "description": description,
                "raw_payload": item,
                "source_url": link,
                "apply_url": link,
                "published_at": published_at,
                "fetched_at": now,
            }
            row = db.scalar(
                select(WebJobListing).where(
                    WebJobListing.source == SOURCE,
                    WebJobListing.external_id == external_id,
                )
            )
            if row is None:
                db.add(
                    WebJobListing(
                        source=SOURCE,
                        external_id=external_id,
                        status="pending_classification",
                        **record,
                    )
                )
                totals["collected"] += 1
            else:
                for key, value in record.items():
                    setattr(row, key, value)
                if row.status == "collected":
                    row.status = "pending_classification"
                totals["updated"] += 1
        db.commit()
    record_web_source_check(SOURCE)
    return totals


if __name__ == "__main__":
    print(collect_remote3())
