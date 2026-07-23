from __future__ import annotations

import asyncio

from app.services.scan_service import scan_once
from worker.classify_pending import classify_pending


def run_daily_job() -> None:
    """凌晨定时任务：先增量扫描 Telegram（规则初筛），再用 LLM 复核候选消息。"""
    asyncio.run(scan_once())
    classify_pending()


if __name__ == "__main__":
    run_daily_job()
