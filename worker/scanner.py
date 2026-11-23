from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.network.connection import ConnectionTcpObfuscated
from python_socks import ProxyType
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel, ChatInviteAlready

from app.db import connect, init_db, preferences
from app.job_classifier import classify
from app.rules import as_payload, evaluate


def env_ready() -> bool:
    return all(os.getenv(key) for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE"))


async def scan() -> None:
    init_db()
    with connect() as conn:
        run_id = conn.execute("INSERT INTO runs(status, detail) VALUES('running', '开始扫描')").lastrowid
        sources = conn.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY id").fetchall()
    if not sources:
        finish(run_id, "success", 0, 0, "没有启用来源，未读取 Telegram")
        return
    if not env_ready():
        finish(run_id, "failed", 0, 0, "Telegram API 凭据或手机号尚未配置")
        return
    processed = matched = 0
    backfill_days = int(os.getenv("JOB_BOT_BACKFILL_DAYS", "0"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=backfill_days) if backfill_days else None
    proxy = (ProxyType.SOCKS5, os.getenv("TELEGRAM_PROXY_HOST", "127.0.0.1"), int(os.getenv("TELEGRAM_PROXY_PORT", "7897")))
    client = TelegramClient("/app/data/telegram", int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"], proxy=proxy, connection=ConnectionTcpObfuscated, connection_retries=2, retry_delay=2)
    try:
        await client.start(phone=os.environ["TELEGRAM_PHONE"])
        for source in sources:
            try:
                entity = await resolve_entity(client, source["handle"])
                await discover_linked_source(client, source, entity)
                newest = source["last_processed_message_id"]
                async def source_messages():
                    if cutoff and source["last_processed_message_id"] == 0:
                        pending = []
                        async for item in client.iter_messages(entity):
                            if item.date and item.date < cutoff:
                                break
                            pending.append(item)
                        for item in reversed(pending):
                            yield item
                    else:
                        async for item in client.iter_messages(entity, min_id=source["last_processed_message_id"], reverse=True):
                            yield item

                async for message in source_messages():
                    if not message.message:
                        newest = max(newest, message.id)
                        continue
                    sender = await message.get_sender()
                    username = getattr(sender, "username", None)
                    sender_id = str(getattr(sender, "id", "")) or None
                    sent_at = message.date.astimezone(timezone.utc).isoformat() if message.date else None
                    url = f"https://t.me/{source['handle']}/{message.id}" if getattr(entity, "username", None) else None
                    if os.getenv("JOB_BOT_LLM_ENABLED", "0") == "1":
                        try:
                            llm_result = classify(message.message)
                            is_match = llm_result.is_match
                            confidence = llm_result.confidence
                            reason = llm_result.reason
                            model_name = "qwen2.5:3b"
                            prompt_version = "segment-v1"
                            result_payload = {
                                "is_match": is_match,
                                "confidence": confidence,
                                "reason": reason,
                                "matched_excerpt": llm_result.matched_excerpt,
                            }
                            display_text = llm_result.matched_excerpt if is_match else message.message
                        except Exception as exc:
                            decision = evaluate(message.message, preferences())
                            is_match = decision.is_match
                            confidence = decision.confidence
                            reason = f"LLM 复核失败，规则结果：{decision.reason}"
                            model_name = "rule-engine-fallback"
                            prompt_version = "v1"
                            result_payload = as_payload(decision)
                            display_text = message.message
                    else:
                        decision = evaluate(message.message, preferences())
                        is_match = decision.is_match
                        confidence = decision.confidence
                        reason = decision.reason
                        model_name = "rule-engine"
                        prompt_version = "v1"
                        result_payload = as_payload(decision)
                        display_text = message.message
                    with connect() as conn:
                        columns = {column[1] for column in conn.execute("PRAGMA table_info(messages)")}
                        if "original_text" not in columns:
                            conn.execute("ALTER TABLE messages ADD COLUMN original_text TEXT")
                        cursor = conn.execute("INSERT OR IGNORE INTO messages(source_id, telegram_message_id, sent_at, text, original_text, sender_username, sender_id, message_url) VALUES(?,?,?,?,?,?,?,?)", (source["id"], message.id, sent_at, display_text, message.message, username, sender_id, url))
                        row = conn.execute("SELECT id FROM messages WHERE source_id=? AND telegram_message_id=?", (source["id"], message.id)).fetchone()
                        conn.execute("INSERT OR IGNORE INTO analyses(message_id, is_match, confidence, reason, model_name, prompt_version, result_json) VALUES(?,?,?,?,?,?,?)", (row[0], int(is_match), confidence, reason, model_name, prompt_version, json.dumps(result_payload, ensure_ascii=False)))
                    processed += 1
                    matched += int(is_match)
                    newest = max(newest, message.id)
                with connect() as conn:
                    conn.execute("UPDATE sources SET last_processed_message_id=?, last_success_at=CURRENT_TIMESTAMP, last_error=NULL WHERE id=?", (newest, source["id"]))
            except Exception as exc:
                with connect() as conn:
                    conn.execute("UPDATE sources SET last_error=? WHERE id=?", (str(exc)[:500], source["id"]))
        finish(run_id, "success", processed, matched, "扫描完成")
    except Exception as exc:
        finish(run_id, "failed", processed, matched, str(exc)[:500])
        raise
    finally:
        await client.disconnect()


async def resolve_entity(client: TelegramClient, handle: str):
    """Resolve public handles, saved peer IDs, and already-joined invite links."""
    if handle.startswith("+"):
        invite = await client(CheckChatInviteRequest(handle[1:]))
        if not isinstance(invite, ChatInviteAlready):
            raise RuntimeError("此私有邀请链接尚未加入；请先用 Telegram 客户端加入，系统不会自动加入群组")
        return invite.chat
    if handle.startswith("peer:"):
        return await client.get_entity(int(handle.split(":", 1)[1]))
    return await client.get_entity(handle)


async def discover_linked_source(client: TelegramClient, source, entity) -> None:
    """Register a linked discussion group as an independent source and cursor."""
    if not isinstance(entity, Channel):
        return
    try:
        full = await client(GetFullChannelRequest(entity))
        linked_id = full.full_chat.linked_chat_id
        if not linked_id:
            return
        linked = await client.get_entity(linked_id)
        handle = getattr(linked, "username", None) or f"peer:{linked.id}"
        label = f"{source['label']}（关联讨论群）"
        with connect() as conn:
            conn.execute(
                "INSERT INTO sources(handle, label, parent_source_id, is_auto_discovered) VALUES(?,?,?,1) "
                "ON CONFLICT(handle) DO UPDATE SET parent_source_id=excluded.parent_source_id",
                (handle, label, source["id"]),
            )
    except Exception:
        # A missing linked chat must not block the explicitly configured source.
        return


def finish(run_id: int, status: str, processed: int, matched: int, detail: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET finished_at=CURRENT_TIMESTAMP, status=?, processed_count=?, matched_count=?, detail=? WHERE id=?", (status, processed, matched, detail, run_id))


if __name__ == "__main__":
    asyncio.run(scan())
