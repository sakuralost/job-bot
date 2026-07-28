from __future__ import annotations

import json
from pathlib import Path

from app.db import connect, init_db


def main() -> None:
    init_db()
    sources = json.loads(Path("/app/config/sources.json").read_text(encoding="utf-8"))["sources"]
    with connect() as conn:
        for source in sources:
            conn.execute(
                "INSERT INTO sources(handle, label) VALUES(?, ?) "
                "ON CONFLICT(handle) DO UPDATE SET label=excluded.label",
                (source["handle"], source["label"]),
            )
    print(f"已预置 {len(sources)} 个来源；尚未读取 Telegram 消息。")


if __name__ == "__main__":
    main()
