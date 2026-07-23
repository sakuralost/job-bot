"""一次性导入 config/monitored_chats.yaml 到数据库；已存在的 chat_id 会更新 title/username/enabled，不会重复插入。"""
from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select

from app.database.models import MonitoredChat
from app.database.session import SessionLocal


def seed(path: str = "/app/config/monitored_chats.yaml") -> None:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    with SessionLocal() as db:
        for chat in data["chats"]:
            row = db.scalar(select(MonitoredChat).where(MonitoredChat.telegram_chat_id == chat["telegram_chat_id"]))
            if row is None:
                row = MonitoredChat(telegram_chat_id=chat["telegram_chat_id"])
                db.add(row)
            row.title = chat["title"]
            row.username = chat.get("username")
            row.enabled = chat.get("enabled", True)
        db.commit()
    print(f"已导入 {len(data['chats'])} 个来源")


if __name__ == "__main__":
    seed()
