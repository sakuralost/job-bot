from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import WebSource
from app.database.session import SessionLocal


WEB_SOURCE_HISTORY_DAYS = 7
WEB_SOURCE_DEFINITIONS = (
    {
        "source_key": "web3.career",
        "title": "Web3.career（第三方职位平台）",
        "website_url": "https://web3.career/",
    },
    {
        "source_key": "remote3.rss",
        "title": "Remote3（第三方远程职位平台）",
        "website_url": "https://remote3.co/",
    },
    {
        "source_key": "himalayas.api",
        "title": "Himalayas（第三方远程职位平台）",
        "website_url": "https://himalayas.app/",
    },
    {
        "source_key": "remoteok.api",
        "title": "Remote OK（第三方远程职位平台）",
        "website_url": "https://remoteok.com/",
    },
    {
        "source_key": "jobicy.api",
        "title": "Jobicy（第三方远程职位平台）",
        "website_url": "https://jobicy.com/",
    },
    {
        "source_key": "greenhouse:coinbase",
        "title": "Coinbase（官网招聘）",
        "website_url": "https://www.coinbase.com/careers",
    },
    {
        "source_key": "greenhouse:consensys",
        "title": "Consensys（官网招聘）",
        "website_url": "https://consensys.io/careers",
    },
    {
        "source_key": "greenhouse:fireblocks",
        "title": "Fireblocks（官网招聘）",
        "website_url": "https://www.fireblocks.com/careers/",
    },
    {
        "source_key": "greenhouse:ripple",
        "title": "Ripple（官网招聘）",
        "website_url": "https://ripple.com/careers/",
    },
    {
        "source_key": "greenhouse:nansen",
        "title": "Nansen（官网招聘）",
        "website_url": "https://www.nansen.ai/careers",
    },
    {
        "source_key": "greenhouse:bitgo",
        "title": "BitGo（官网招聘）",
        "website_url": "https://www.bitgo.com/careers/",
    },
    {
        "source_key": "greenhouse:figment",
        "title": "Figment（官网招聘）",
        "website_url": "https://www.figment.io/careers",
    },
    {
        "source_key": "greenhouse:gemini",
        "title": "Gemini（官网招聘）",
        "website_url": "https://www.gemini.com/careers",
    },
    {
        "source_key": "ashby:phantom",
        "title": "Phantom（官网招聘）",
        "website_url": "https://phantom.com/careers",
    },
    {
        "source_key": "ashby:opensea",
        "title": "OpenSea（官网招聘）",
        "website_url": "https://opensea.io/careers",
    },
    {
        "source_key": "ashby:uniswap",
        "title": "Uniswap Labs（官网招聘）",
        "website_url": "https://www.uniswapfoundation.org/careers",
    },
    {
        "source_key": "ashby:mystenlabs",
        "title": "Mysten Labs / Sui（官网招聘）",
        "website_url": "https://mystenlabs.com/careers",
    },
    {
        "source_key": "ashby:alchemy",
        "title": "Alchemy（官网招聘）",
        "website_url": "https://www.alchemy.com/careers",
    },
    {
        "source_key": "lever:gate",
        "title": "Gate（官网招聘）",
        "website_url": "https://www.gate.com/careers",
    },
    {
        "source_key": "lever:binance",
        "title": "Binance（官网招聘）",
        "website_url": "https://www.binance.com/en/careers",
    },
    {
        "source_key": "lever:kraken",
        "title": "Kraken（官网招聘）",
        "website_url": "https://www.kraken.com/careers",
    },
)


def ensure_web_sources(db) -> None:
    """只补齐内置来源，绝不覆盖用户在仪表盘设置的屏蔽状态。"""
    existing = {source.source_key for source in db.scalars(select(WebSource)).all()}
    for definition in WEB_SOURCE_DEFINITIONS:
        if definition["source_key"] not in existing:
            db.add(WebSource(enabled=True, lookback_days=WEB_SOURCE_HISTORY_DAYS, **definition))


def web_source_payload(source: WebSource) -> dict[str, object]:
    return {
        "id": source.id,
        "source_key": source.source_key,
        "title": source.title,
        "website_url": source.website_url,
        "enabled": source.enabled,
        "lookback_days": source.lookback_days,
        "last_checked_at": source.last_checked_at,
        "last_error": source.last_error,
    }


def is_web_source_enabled(source_key: str) -> bool:
    with SessionLocal() as db:
        ensure_web_sources(db)
        db.commit()
        source = db.scalar(select(WebSource).where(WebSource.source_key == source_key))
        return bool(source and source.enabled)


def record_web_source_check(source_key: str, error: str | None = None) -> None:
    with SessionLocal() as db:
        source = db.scalar(select(WebSource).where(WebSource.source_key == source_key))
        if source is None:
            return
        source.last_checked_at = datetime.now(timezone.utc)
        source.last_error = error[:2000] if error else None
        db.commit()
