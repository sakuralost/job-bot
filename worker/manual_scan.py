from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.database.models import ManualScanRequest, ScanRun, TelegramMessage
from app.database.session import SessionLocal
from app.services.scan_service import scan_once
from app.services.worker_lock import llm_classification_lock, telegram_scan_lock
from worker.classify_pending import _classify_pending_unlocked

ACTIVE_STATUSES = {"queued", "scanning", "waiting_for_llm", "classifying"}


def _set_failed(request_id: int, error: Exception) -> None:
    with SessionLocal() as db:
        request = db.get(ManualScanRequest, request_id)
        if request is None:
            return
        request.status = "failed"
        request.finished_at = datetime.now(timezone.utc)
        request.error_message = str(error)[:2000]
        db.commit()


def _update_scan_progress(request_id: int, messages_found: int, rule_candidates: int) -> None:
    with SessionLocal() as db:
        request = db.get(ManualScanRequest, request_id)
        if request is None:
            return
        request.messages_found = messages_found
        request.rule_candidates = rule_candidates
        db.commit()


def _normalized_counts(counts: dict[str, int]) -> dict[str, int]:
    return {
        "accepted": counts.get("accepted", 0),
        "excluded": (
            counts.get("excluded", 0)
            + counts.get("excluded_not_remote", 0)
            + counts.get("not_product_role", 0)
        ),
        "duplicate": counts.get("duplicate", 0),
        "error": counts.get("error", 0),
    }


def _merged_counts(base: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    normalized = _normalized_counts(current)
    return {key: base.get(key, 0) + normalized.get(key, 0) for key in normalized}


def _update_progress(request_id: int, processed: int, total: int, counts: dict[str, int]) -> None:
    with SessionLocal() as db:
        request = db.get(ManualScanRequest, request_id)
        if request is None:
            return
        request.classification_total = total
        request.classification_processed = processed
        request.accepted_items = counts.get("accepted", 0)
        request.excluded_items = counts.get("excluded", 0)
        request.duplicate_items = counts.get("duplicate", 0)
        request.error_items = counts.get("error", 0)
        db.commit()


def launch_manual_scan(request_id: int) -> None:
    log_path = Path(os.getenv("JOB_BOT_MANUAL_SCAN_LOG", "/app/data/manual-scan.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "worker.manual_scan", str(request_id)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()


def resume_active_manual_scan() -> int | None:
    with SessionLocal() as db:
        request = db.scalar(
            select(ManualScanRequest)
            .where(ManualScanRequest.status.in_(ACTIVE_STATUSES))
            .order_by(ManualScanRequest.id.desc())
            .limit(1)
        )
        request_id = request.id if request is not None else None
    if request_id is not None:
        launch_manual_scan(request_id)
    return request_id


def run_manual_scan(request_id: int) -> None:
    with SessionLocal() as db:
        request = db.get(ManualScanRequest, request_id)
        if request is None or request.status not in ACTIVE_STATUSES:
            return
        should_scan = request.status in {"queued", "scanning"}
        if should_scan:
            request.status = "scanning"
            request.started_at = request.started_at or datetime.now(timezone.utc)
        db.commit()

    try:
        if should_scan:
            with telegram_scan_lock(blocking=True):
                scan_run_id = asyncio.run(
                    scan_once(
                        progress_callback=lambda messages, candidates: _update_scan_progress(
                            request_id, messages, candidates
                        )
                    )
                )

            with SessionLocal() as db:
                request = db.get(ManualScanRequest, request_id)
                scan_run = db.get(ScanRun, scan_run_id)
                request.scan_run_id = scan_run_id
                request.messages_found = scan_run.total_messages
                request.rule_candidates = scan_run.rule_candidates
                request.scan_error_count = scan_run.error_count
                request.duplicate_items = scan_run.duplicate_jobs
                request.status = "waiting_for_llm"
                request.classification_total = db.scalar(
                    select(func.count()).select_from(TelegramMessage).where(
                        TelegramMessage.status == "pending_classification"
                    )
                )
                db.commit()

        with SessionLocal() as db:
            request = db.get(ManualScanRequest, request_id)
            pending_total = db.scalar(
                select(func.count()).select_from(TelegramMessage).where(
                    TelegramMessage.status == "pending_classification"
                )
            )
            base_processed = request.classification_processed
            classification_total = max(request.classification_total, base_processed + pending_total)
            request.classification_total = classification_total
            base_counts = {
                "accepted": request.accepted_items,
                "excluded": request.excluded_items,
                "duplicate": request.duplicate_items,
                "error": request.error_items,
            }
            db.commit()

        if pending_total == 0:
            with SessionLocal() as db:
                request = db.get(ManualScanRequest, request_id)
                request.status = "completed"
                request.finished_at = datetime.now(timezone.utc)
                db.commit()
            return

        with llm_classification_lock(blocking=True):
            with SessionLocal() as db:
                request = db.get(ManualScanRequest, request_id)
                request.status = "classifying"
                request.llm_started_at = request.llm_started_at or datetime.now(timezone.utc)
                db.commit()

            counts = _classify_pending_unlocked(
                progress_callback=lambda processed, _total, values: _update_progress(
                    request_id,
                    base_processed + processed,
                    classification_total,
                    _merged_counts(base_counts, values),
                )
            )

        with SessionLocal() as db:
            request = db.get(ManualScanRequest, request_id)
            request.status = "completed"
            request.finished_at = datetime.now(timezone.utc)
            final_counts = _merged_counts(base_counts, counts)
            request.accepted_items = final_counts["accepted"]
            request.excluded_items = final_counts["excluded"]
            request.duplicate_items = final_counts["duplicate"]
            request.error_items = final_counts["error"]
            if counts.get("skipped_unconfigured", 0):
                request.status = "failed"
                request.error_message = "LLM 配置不完整，新消息已保留，尚未完成模型筛选"
            db.commit()
    except Exception as exc:  # noqa: BLE001 - 任务状态必须持久化，避免后台永久显示运行中
        _set_failed(request_id, exc)
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python -m worker.manual_scan <request_id>")
    run_manual_scan(int(sys.argv[1]))
