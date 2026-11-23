from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class RuleResult:
    is_match: bool
    confidence: float
    reason: str
    risk_flags: list[str]


def evaluate(text: str, preference: dict) -> RuleResult:
    """Deterministic first pass; LLM is only used for non-obvious messages."""
    content = text.lower()
    excluded = [x.lower().strip() for x in preference.get("excluded_keywords", []) if x.strip()]
    matched_excluded = [x for x in excluded if x in content]
    if matched_excluded:
        return RuleResult(False, 0.98, f"命中排除条件：{', '.join(matched_excluded)}", ["excluded_keyword"])
    groups = preference.get("required_any_groups")
    if not groups:
        legacy = [x.lower().strip() for x in preference.get("required_keywords", []) if x.strip()]
        groups = [legacy] if legacy else []
    selected = []
    for group in groups:
        matches = [x.lower().strip() for x in group if x.lower().strip() in content]
        if not matches:
            return RuleResult(False, 0.78, "未命中必要类别：" + " / ".join(group), ["missing_required_category"])
        selected.extend(matches)
    if groups:
        return RuleResult(True, 0.9, f"命中岗位与招聘信号：{', '.join(selected)}", [])
    return RuleResult(False, 0.35, "尚未配置可判定的求职条件，保留待复核", ["needs_review"])


def as_payload(result: RuleResult) -> dict:
    return asdict(result)
