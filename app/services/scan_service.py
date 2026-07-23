from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from python_socks import ProxyType
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.network.connection import ConnectionTcpObfuscated

from app.config import settings
from app.database.models import MonitoredChat, ScanRun, TelegramMessage
from app.database.session import SessionLocal
from app.classifier.rule_filter import decide, load_filters


async def scan_once() -> int:
    cfg = settings()
    if not (cfg.telegram_api_id and cfg.telegram_api_hash and cfg.telegram_phone):
        raise RuntimeError("Telegram 尚未授权")
    with SessionLocal() as db:
        run = ScanRun(status="running")
        db.add(run); db.commit(); db.refresh(run)
        chats = list(db.scalars(select(MonitoredChat).where(MonitoredChat.enabled.is_(True))))
    filters = load_filters()
    proxy = (ProxyType.SOCKS5, cfg.telegram_proxy_host, cfg.telegram_proxy_port) if cfg.telegram_proxy_host else None
    client = TelegramClient(
        cfg.telegram_session_path, cfg.telegram_api_id, cfg.telegram_api_hash,
        proxy=proxy, connection=ConnectionTcpObfuscated, connection_retries=2, retry_delay=2,
    )
    await client.start(phone=cfg.telegram_phone)
    total = candidates = 0
    try:
        for chat in chats:
            max_persisted = chat.last_scanned_message_id
            try:
                entity = await client.get_entity(chat.telegram_chat_id)
                async for message in client.iter_messages(entity, min_id=chat.last_scanned_message_id, reverse=True, limit=cfg.max_messages_per_chat_per_run):
                    text = message.message or ""
                    sender = await message.get_sender()
                    with SessionLocal() as db:
                        exists = db.scalar(select(TelegramMessage).where(TelegramMessage.telegram_chat_id == chat.telegram_chat_id, TelegramMessage.telegram_message_id == message.id))
                        if not exists:
                            decision = decide(text, filters)
                            db.add(TelegramMessage(telegram_chat_id=chat.telegram_chat_id, telegram_message_id=message.id, sender_telegram_user_id=getattr(sender, "id", None), sender_username=getattr(sender, "username", None), sender_display_name=getattr(sender, "first_name", None), message_text=text, message_date=message.date, status="rule_rejected" if not decision.candidate else "pending_classification", raw_payload={"rule_reason": decision.reason}))
                            db.commit()
                            candidates += int(decision.candidate)
                        max_persisted = max(max_persisted, message.id); total += 1
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
            row = db.get(ScanRun, run.id); row.status = "success"; row.total_chats = len(chats); row.total_messages = total; row.rule_candidates = candidates; row.finished_at = datetime.now(timezone.utc); db.commit()
        return run.id
    finally:
        await client.disconnect()
