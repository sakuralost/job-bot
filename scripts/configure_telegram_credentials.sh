#!/usr/bin/env bash
set -euo pipefail

env_file="/home/ubuntu/job-bot/job-bot.env"
[[ -f "$env_file" ]] || { echo "缺少 $env_file"; exit 1; }
read -r -p "Telegram API_ID: " telegram_api_id
read -r -s -p "Telegram API_HASH（输入不回显）: " telegram_api_hash; echo
read -r -p "Telegram 登录手机号（国际格式，例如 +86138...）: " telegram_phone
export telegram_api_id telegram_api_hash telegram_phone env_file
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["env_file"])
updates = {
    "TELEGRAM_API_ID": os.environ["telegram_api_id"],
    "TELEGRAM_API_HASH": os.environ["telegram_api_hash"],
    "TELEGRAM_PHONE": os.environ["telegram_phone"],
}
lines = path.read_text().splitlines()
for key, value in updates.items():
    prefix = f"{key}="
    lines = [f"{key}={value}" if line.startswith(prefix) else line for line in lines]
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
PY
echo "凭据已仅保存到本机 job-bot.env；下一步请运行 worker.login 完成验证码登录。"
