from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _file_lock(path: Path, *, blocking: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), operation)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def llm_classification_lock(*, blocking: bool):
    """避免每日扫描、主动扫描和历史重筛同时占用同一个本地模型。"""
    path = Path(os.getenv("JOB_BOT_LLM_LOCK_PATH", "/app/data/llm-classification.lock"))
    with _file_lock(path, blocking=blocking) as acquired:
        yield acquired


@contextmanager
def telegram_scan_lock(*, blocking: bool):
    """避免定时扫描与管理员主动扫描同时移动 Telegram 来源游标。"""
    path = Path(os.getenv("JOB_BOT_SCAN_LOCK_PATH", "/app/data/telegram-scan.lock"))
    with _file_lock(path, blocking=blocking) as acquired:
        yield acquired
