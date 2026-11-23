from __future__ import annotations

import asyncio

from app.services.scan_service import scan_once
from app.services.worker_lock import llm_classification_lock, telegram_scan_lock
from worker.classify_pending import _classify_pending_unlocked
from worker.official_boards import collect_official_boards
from worker.remote3 import collect_remote3
from worker.public_remote_boards import collect_public_remote_boards
from worker.web3_career import _classify_pending_web_listings_unlocked, collect_web3_career


def run_daily_job() -> None:
    """定时主任务：统一收集来源，再按 Telegram、网站顺序使用同一个 LLM 队列。"""
    with telegram_scan_lock(blocking=False) as acquired:
        if not acquired:
            print("管理员主动扫描正在读取 Telegram；本轮定时扫描跳过，来源位置不会丢失", flush=True)
            return
        asyncio.run(scan_once())
    collect_web3_career()
    collect_remote3()
    collect_public_remote_boards()
    collect_official_boards()
    with llm_classification_lock(blocking=False) as acquired:
        if not acquired:
            print("本地模型正被其他任务使用；本轮职位已保留到下一次主扫描", flush=True)
            return
        _classify_pending_unlocked()
        _classify_pending_web_listings_unlocked()


if __name__ == "__main__":
    run_daily_job()
