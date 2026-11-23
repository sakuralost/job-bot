from __future__ import annotations

import os

from telethon.sync import TelegramClient
from telethon.network.connection import ConnectionTcpObfuscated
from python_socks import ProxyType


required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE")
if not all(os.getenv(key) for key in required):
    raise SystemExit("请先运行 scripts/configure_telegram_credentials.sh 填写 Telegram API_ID、API_HASH 和手机号")

# 默认走本机 mihomo 7897（与 compose.yaml / job-bot.env 一致）
proxy = (ProxyType.SOCKS5, os.getenv("TELEGRAM_PROXY_HOST", "127.0.0.1"), int(os.getenv("TELEGRAM_PROXY_PORT", "7897")))
client = TelegramClient("/app/data/telegram", int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"], proxy=proxy, connection=ConnectionTcpObfuscated, connection_retries=2, retry_delay=2)
try:
    client.start(phone=os.environ["TELEGRAM_PHONE"])
    print("Telegram 登录成功；会话仅保存在本机 data/telegram.session，不要提交到 Git。")
finally:
    client.disconnect()
