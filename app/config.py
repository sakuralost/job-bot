from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "job-bot"
    app_env: str = "production"
    timezone: str = "Asia/Shanghai"
    # compose.yaml 里 app 用 host 网络，所以是 127.0.0.1 而不是容器名 "postgres"
    database_url: str = "postgresql+psycopg://jobbot:change_me@127.0.0.1:5432/jobbot"
    admin_api_token: str = ""
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_path: str = "/app/data/telegram"  # Telethon 自动补 .session 后缀
    # 留空表示不用代理直连；如果你的网络需要代理才能连上 Telegram，填 SOCKS5 代理的地址和端口
    telegram_proxy_host: str = ""
    telegram_proxy_port: int | None = None
    hermes_bot_token: str = ""
    hermes_target_chat_id: int | None = None
    hermes_allowed_user_ids: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str | None = None
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
