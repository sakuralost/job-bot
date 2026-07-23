from __future__ import annotations

import os

from telethon.sync import TelegramClient
from telethon.network.connection import ConnectionTcpObfuscated
from python_socks import ProxyType


required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE")
if not all(os.getenv(key) for key in required):
    raise SystemExit("请先运行 scripts/configure_telegram_credentials.sh 填写 Telegram API_ID、API_HASH 和手机号")

# 留空表示不用代理直连；如果你的网络需要代理才能连上 Telegram，设置这两个环境变量
proxy_host = os.getenv("TELEGRAM_PROXY_HOST", "")
proxy = (ProxyType.SOCKS5, proxy_host, int(os.getenv("TELEGRAM_PROXY_PORT", "1080"))) if proxy_host else None
client = TelegramClient("/app/data/telegram", int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"], proxy=proxy, connection=ConnectionTcpObfuscated, connection_retries=2, retry_delay=2)
try:
    client.start(phone=os.environ["TELEGRAM_PHONE"])
    print("Telegram 登录成功；会话只保存在本地 data/telegram.session，不会进入 Git 或任何同步目录。")
finally:
    client.disconnect()
