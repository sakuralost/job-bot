from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from python_socks import ProxyType
from sqlalchemy import func, or_, select
from telethon import TelegramClient
from telethon.network.connection import ConnectionTcpObfuscated
from telethon.tl.types import Channel
from telethon.utils import get_peer_id

from app.config import settings
from app.database.models import MonitoredChat

SOURCE_HISTORY_DAYS = 3
_PUBLIC_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


class SourceResolutionError(RuntimeError):
    """管理员添加 Telegram 来源时可直接展示的安全错误。"""


@dataclass(frozen=True)
class ResolvedSource:
    telegram_chat_id: int
    title: str
    username: str
    initial_message_id: int


def normalize_source_reference(value: str) -> str:
    reference = value.strip()
    if not reference:
        raise ValueError("请输入 Telegram 公开频道链接或 @用户名")

    if reference.startswith("@"):
        username = reference[1:]
    elif "://" in reference or reference.lower().startswith(("t.me/", "telegram.me/")):
        parsed = urlparse(reference if "://" in reference else f"https://{reference}")
        if parsed.hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            raise ValueError("目前只支持 t.me 公开链接或 @用户名")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1 or parts[0].startswith(("+", "joinchat")):
            raise ValueError("目前只支持公开频道的 t.me 链接或 @用户名")
        username = parts[0]
    else:
        username = reference

    if not _PUBLIC_USERNAME.fullmatch(username):
        raise ValueError("来源用户名格式不正确，请填写公开频道的 t.me 链接或 @用户名")
    return username


async def _resolve_source(reference: str) -> ResolvedSource:
    username = normalize_source_reference(reference)
    cfg = settings()
    if not (cfg.telegram_api_id and cfg.telegram_api_hash):
        raise SourceResolutionError("Telegram 登录信息尚未配置，暂时无法验证来源")

    proxy = None
    if cfg.telegram_proxy_host:
        proxy = (ProxyType.SOCKS5, cfg.telegram_proxy_host, cfg.telegram_proxy_port)
    client = TelegramClient(
        cfg.telegram_session_path,
        cfg.telegram_api_id,
        cfg.telegram_api_hash,
        proxy=proxy,
        connection=ConnectionTcpObfuscated,
        connection_retries=2,
        retry_delay=2,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise SourceResolutionError("Telegram 登录会话已失效，请先重新授权")
        entity = await client.get_entity(username)
        if not isinstance(entity, Channel):
            raise SourceResolutionError("该链接不是公开频道或群组，不能作为消息来源")
        resolved_username = getattr(entity, "username", None)
        if not resolved_username:
            raise SourceResolutionError("该来源没有公开用户名，暂时不能通过链接添加")

        cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_HISTORY_DAYS)
        older_messages = await client.get_messages(entity, limit=1, offset_date=cutoff)
        initial_message_id = older_messages[0].id if older_messages else 0
        return ResolvedSource(
            telegram_chat_id=get_peer_id(entity),
            title=getattr(entity, "title", None) or resolved_username,
            username=resolved_username,
            initial_message_id=initial_message_id,
        )
    except SourceResolutionError:
        raise
    except Exception as exc:
        raise SourceResolutionError(
            "无法访问该公开来源，请检查链接是否正确；私密来源需要当前 Telegram 账号已有权限"
        ) from exc
    finally:
        await client.disconnect()


def resolve_source(reference: str) -> ResolvedSource:
    return asyncio.run(_resolve_source(reference))


def upsert_source(db, source: ResolvedSource) -> tuple[MonitoredChat, bool]:
    existing = db.scalar(
        select(MonitoredChat).where(
            or_(
                MonitoredChat.telegram_chat_id == source.telegram_chat_id,
                func.lower(MonitoredChat.username) == source.username.lower(),
            )
        )
    )
    if existing is not None:
        existing.telegram_chat_id = source.telegram_chat_id
        existing.title = source.title
        existing.username = source.username
        existing.enabled = True
        return existing, False

    row = MonitoredChat(
        telegram_chat_id=source.telegram_chat_id,
        title=source.title,
        username=source.username,
        enabled=True,
        last_scanned_message_id=source.initial_message_id,
    )
    db.add(row)
    return row, True


def source_payload(source: MonitoredChat) -> dict[str, object]:
    return {
        "id": source.id,
        "title": source.title,
        "username": source.username,
        "enabled": source.enabled,
    }
