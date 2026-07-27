from __future__ import annotations

import json
import os
import sys

from app.db import connect, init_db
from app.job_classifier import classify


def prepare_schema() -> None:
    init_db()
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "original_text" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN original_text TEXT")
        conn.execute("UPDATE messages SET original_text=text WHERE original_text IS NULL")


def reclassify() -> None:
    prepare_schema()
    only_candidates = os.getenv("JOB_BOT_LLM_ONLY_CANDIDATES", "0") == "1"
    clause = "WHERE a.is_match=1" if only_candidates else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, m.original_text FROM messages m JOIN analyses a ON a.message_id=m.id {clause} ORDER BY m.id"  # noqa: S608 - fixed clause
        ).fetchall()
    total = len(rows)
    matched = 0
    print(f"LLM 开始复核 {total} 条消息", flush=True)
    for index, row in enumerate(rows, start=1):
        try:
            result = classify(row["original_text"])
            display_text = result.matched_excerpt if result.is_match else row["original_text"]
            payload = {
                "is_match": result.is_match,
                "confidence": result.confidence,
                "reason": result.reason,
                "matched_excerpt": result.matched_excerpt,
            }
            with connect() as conn:
                conn.execute(
                    "UPDATE messages SET text=? WHERE id=?",
                    (display_text, row["id"]),
                )
                conn.execute(
                    "UPDATE analyses SET is_match=?, confidence=?, reason=?, model_name=?, prompt_version=?, result_json=?, analyzed_at=CURRENT_TIMESTAMP WHERE message_id=?",
                    (int(result.is_match), result.confidence, result.reason, "qwen2.5:3b", "segment-v1", json.dumps(payload, ensure_ascii=False), row["id"]),
                )
            matched += int(result.is_match)
        except Exception as exc:
            print(f"[{index}/{total}] message={row['id']} error={exc}", file=sys.stderr, flush=True)
        if index % 10 == 0 or index == total:
            print(f"LLM 进度 {index}/{total}，命中 {matched}", flush=True)
    print(f"LLM 复核完成：{total} 条，命中 {matched} 条", flush=True)


if __name__ == "__main__":
    reclassify()
