from __future__ import annotations

import asyncio

from app.services.scan_service import scan_once
from app.services.worker_lock import telegram_scan_lock
from worker.classify_pending import classify_pending


def run_daily_job() -> None:
    """凌晨定时任务：先增量扫描 Telegram（规则初筛），再用 LLM 复核候选消息。"""
    with telegram_scan_lock(blocking=False) as acquired:
        if not acquired:
            print("管理员主动扫描正在读取 Telegram；本轮定时扫描跳过，来源位置不会丢失", flush=True)
            return
        asyncio.run(scan_once())
    classify_pending()


if __name__ == "__main__":
    run_daily_job()
