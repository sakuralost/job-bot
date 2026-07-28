from __future__ import annotations

from sqlalchemy import select

from app.database.models import JobDedupGroup

MAX_IGNORE_REASONS = 30
MAX_REASON_LENGTH = 500


def ignored_reason_context(db) -> str:
    """把最近的人工忽略原因整理为新岗位模型可使用的个性化偏好。"""
    rows = db.scalars(
        select(JobDedupGroup.note)
        .where(
            JobDedupGroup.status == "ignored",
            JobDedupGroup.note.is_not(None),
        )
        .order_by(JobDedupGroup.updated_at.desc(), JobDedupGroup.id.desc())
        .limit(MAX_IGNORE_REASONS * 2)
    )
    reasons: list[str] = []
    seen: set[str] = set()
    for raw_reason in rows:
        reason = " ".join((raw_reason or "").split())[:MAX_REASON_LENGTH]
        normalized = reason.casefold()
        if not reason or normalized in seen:
            continue
        seen.add(normalized)
        reasons.append(reason)
        if len(reasons) >= MAX_IGNORE_REASONS:
            break

    if not reasons:
        return ""
    items = "\n".join(f"{index}. {reason}" for index, reason in enumerate(reasons, start=1))
    return (
        "\n\n# 用户近期人工忽略反馈\n"
        "以下内容来自用户手动忽略岗位时填写的原因，只用于判断新岗位的个性化匹配度。"
        "它们不是无条件排除规则：只有新岗位明确命中相同特征时，才降低 confidence，"
        "冲突非常明确时才设置 is_excluded=true；与反馈无关的岗位不得受影响。"
        "如果反馈影响了结论，必须在中文 role_match_reason 中说明。\n"
        f"{items}"
    )
