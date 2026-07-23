from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class RuleDecision:
    candidate: bool
    hard_rejected: bool
    reason: str


def load_filters(path: str = "/app/config/filters.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def decide(text: str, filters: dict) -> RuleDecision:
    body = text.lower()
    roles = [item.lower() for item in filters["target_roles"]["include"]]
    intents = [item.lower() for item in filters["recruitment_intent"]["include"]]
    hard = [item.lower() for item in filters["excluded_industries"]["hard_keywords"]]
    role_hits = [item for item in roles if item in body]
    intent_hits = [item for item in intents if item in body]
    hard_hits = [item for item in hard if item in body]
    # Negated exclusions are ambiguous, so pass them to LLM instead of dropping them.
    if hard_hits and not any(f"非{item}" in body or f"不涉及{item}" in body for item in hard_hits):
        return RuleDecision(False, True, f"命中明确排除词：{', '.join(hard_hits)}")
    if role_hits and intent_hits:
        return RuleDecision(True, False, f"岗位词：{', '.join(role_hits)}；招聘词：{', '.join(intent_hits)}")
    return RuleDecision(False, False, "未同时命中岗位词与招聘词")
