from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "job-bot"
    app_env: str = "production"
    timezone: str = "Asia/Shanghai"
    database_url: str = "postgresql+psycopg://jobbot:change_me@postgres:5432/jobbot"
    admin_api_token: str = ""
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_path: str = "/app/data/telegram"  # Telethon 自动补 .session；已存在的登录会话文件是 data/telegram.session
    # 本机 mihomo；compose.yaml 与 job-bot.env 均为 7897
    telegram_proxy_host: str = "127.0.0.1"
    telegram_proxy_port: int = 7897
    hermes_bot_token: str = ""
    hermes_target_chat_id: int | None = None
    hermes_allowed_user_ids: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str | None = None
    # 由 Web3.career 官方 API 表单申请；未配置时网站职位同步保持关闭。
    web3_career_api_token: str = ""
    scan_times: str = "04:00"  # LLM较慢，每天只在凌晨跑一次，其余时间让给RAG等其他业务
    max_messages_per_chat_per_run: int = 5000
    check_private_chat_history: bool = True
    private_chat_history_limit: int = 100
    log_level: str = "INFO"

    @property
    def allowed_user_ids(self) -> set[int]:
        return {int(value) for value in self.hermes_allowed_user_ids.split(",") if value.strip()}


@lru_cache
def settings() -> Settings:
    return Settings()
