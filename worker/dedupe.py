from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

from app.db import connect, init_db


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def contacts(text: str) -> set[str]:
    return {value.lower() for value in re.findall(r"@([a-zA-Z0-9_]{4,})|([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})", text) for value in value if value}


def main() -> None:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.text FROM messages m JOIN analyses a ON a.message_id=m.id "
            "WHERE a.is_match=1 ORDER BY m.id"
        ).fetchall()
        canonical: list[tuple[int, str, set[str]]] = []
        duplicates = 0
        for row in rows:
            body = normalize(row["text"])[:1800]
            fingerprint = hashlib.sha256(body.encode()).hexdigest()[:20]
            message_contacts = contacts(row["text"])
            same = next((mid for mid, prior, _ in canonical if body and body == prior), None)
            conn.execute("UPDATE messages SET fingerprint=?, duplicate_of_message_id=? WHERE id=?", (fingerprint, same, row["id"]))
            if same:
                conn.execute("UPDATE analyses SET status='ignored', note=COALESCE(note, ?) WHERE message_id=? AND status='new'", (f"与消息 #{same} 内容重复", row["id"]))
                duplicates += 1
            else:
                canonical.append((row["id"], body, message_contacts))
    print(f"去重完成：检查 {len(rows)} 条，标记 {duplicates} 条重复")


if __name__ == "__main__":
    main()
