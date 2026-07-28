from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_, select
from python_socks import ProxyType
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.network.connection import ConnectionTcpObfuscated

from app.config import settings
from app.database.models import MonitoredChat, ScanRun, TelegramMessage
from app.database.session import SessionLocal
from app.classifier.rule_filter import decide, load_filters
from app.dedup.fingerprints import message_content_fingerprint
from app.services.prompt_service import get_active_prompt
from app.services.source_management import SOURCE_HISTORY_DAYS

_ALWAYS_DEDUP_STATUSES = {"pending_classification", "classified"}
_PROMPT_SCOPED_DEDUP_STATUSES = {"not_product_role", "excluded"}


def _content_duplicate(db, fingerprint: str, prompt_version: int) -> TelegramMessage | None:
    """在调用 LLM 前识别重复正文；旧提示词的模型排除结果不会阻止新提示词复核。"""
    return db.scalar(
        select(TelegramMessage)
        .where(
            TelegramMessage.content_fingerprint == fingerprint,
            or_(
                TelegramMessage.status.in_(_ALWAYS_DEDUP_STATUSES),
                (
                    (TelegramMessage.status == "duplicate")
                    & (
                        TelegramMessage.dedup_group_id.is_not(None)
                        | (TelegramMessage.classification_prompt_version == prompt_version)
                    )
                ),
                (
                    TelegramMessage.status.in_(_PROMPT_SCOPED_DEDUP_STATUSES)
                    & (TelegramMessage.classification_prompt_version == prompt_version)
                ),
            ),
        )
        .order_by(TelegramMessage.id)
        .limit(1)
    )


async def _bootstrap_cursor(client, entity) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_HISTORY_DAYS)
    older_messages = await client.get_messages(entity, limit=1, offset_date=cutoff)
    return older_messages[0].id if older_messages else 0


async def scan_once(
    progress_callback: Callable[[int, int], None] | None = None,
) -> int:
    cfg = settings()
    if not (cfg.telegram_api_id and cfg.telegram_api_hash and cfg.telegram_phone):
        raise RuntimeError("Telegram 尚未授权")
    with SessionLocal() as db:
        run = ScanRun(status="running")
        db.add(run); db.commit(); db.refresh(run)
        chats = list(db.scalars(select(MonitoredChat).where(MonitoredChat.enabled.is_(True))))
    filters = load_filters()
    prompt_version = get_active_prompt().version
    proxy = (
        (ProxyType.SOCKS5, cfg.telegram_proxy_host, cfg.telegram_proxy_port)
        if cfg.telegram_proxy_host
        else None
    )
    client = TelegramClient(
        cfg.telegram_session_path, cfg.telegram_api_id, cfg.telegram_api_hash,
        proxy=proxy, connection=ConnectionTcpObfuscated, connection_retries=2, retry_delay=2,
    )
    await client.start(phone=cfg.telegram_phone)
    total = candidates = pre_llm_duplicates = 0
    try:
        for chat in chats:
            max_persisted = chat.last_scanned_message_id
            try:
                entity = await client.get_entity(chat.telegram_chat_id)
                if chat.last_scanned_message_id <= 0 and chat.last_scanned_at is None:
                    chat.last_scanned_message_id = await _bootstrap_cursor(client, entity)
                    max_persisted = chat.last_scanned_message_id
                    with SessionLocal() as db:
                        row = db.get(MonitoredChat, chat.id)
                        row.last_scanned_message_id = chat.last_scanned_message_id
                        db.commit()
                async for message in client.iter_messages(
                    entity,
                    min_id=chat.last_scanned_message_id,
                    reverse=True,
                    limit=cfg.max_messages_per_chat_per_run,
                ):
                    text = message.message or ""
                    sender = await message.get_sender()
                    with SessionLocal() as db:
                        exists = db.scalar(
                            select(TelegramMessage).where(
                                TelegramMessage.telegram_chat_id == chat.telegram_chat_id,
                                TelegramMessage.telegram_message_id == message.id,
                            )
                        )
                        if not exists:
                            decision = decide(text, filters)
                            fingerprint = message_content_fingerprint(text)
                            duplicate = (
                                _content_duplicate(db, fingerprint, prompt_version)
                                if decision.candidate
                                else None
                            )
                            status = "rule_rejected"
                            if decision.candidate:
                                status = "duplicate" if duplicate is not None else "pending_classification"
                            db.add(
                                TelegramMessage(
                                    telegram_chat_id=chat.telegram_chat_id,
                                    telegram_message_id=message.id,
                                    sender_telegram_user_id=getattr(sender, "id", None),
                                    sender_username=getattr(sender, "username", None),
                                    sender_display_name=getattr(sender, "first_name", None),
                                    message_text=text,
                                    message_date=message.date,
                                    status=status,
                                    content_fingerprint=fingerprint,
                                    duplicate_of_message_id=duplicate.id if duplicate is not None else None,
                                    dedup_group_id=duplicate.dedup_group_id if duplicate is not None else None,
                                    classification_model=duplicate.classification_model if duplicate is not None else None,
                                    classification_prompt_version=(
                                        duplicate.classification_prompt_version
                                        if duplicate is not None
                                        else None
                                    ),
                                    classification_result_json=(
                                        duplicate.classification_result_json
                                        if duplicate is not None
                                        else None
                                    ),
                                    raw_payload={
                                        "rule_reason": decision.reason,
                                        "pre_llm_duplicate_of": duplicate.id if duplicate is not None else None,
                                    },
                                )
                            )
                            db.commit()
                            candidates += int(decision.candidate)
                            pre_llm_duplicates += int(duplicate is not None)
                        max_persisted = max(max_persisted, message.id)
                        total += 1
                    if progress_callback is not None:
                        progress_callback(total, candidates)
                with SessionLocal() as db:
                    row = db.get(MonitoredChat, chat.id); row.last_scanned_message_id = max_persisted; row.last_scanned_at = datetime.now(timezone.utc); db.commit()
            except FloodWaitError as exc:
                with SessionLocal() as db:
                    db.get(ScanRun, run.id).error_count += 1; db.commit()
                await __import__("asyncio").sleep(exc.seconds)
            except Exception:
                with SessionLocal() as db:
                    db.get(ScanRun, run.id).error_count += 1; db.commit()
        with SessionLocal() as db:
            row = db.get(ScanRun, run.id)
            row.status = "success"
            row.total_chats = len(chats)
            row.total_messages = total
            row.rule_candidates = candidates
            row.llm_candidates = max(0, candidates - pre_llm_duplicates)
            row.duplicate_jobs = pre_llm_duplicates
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        return run.id
    finally:
        await client.disconnect()
