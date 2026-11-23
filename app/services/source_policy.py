from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


CONFIG_PATH = Path("/app/config/source_policy.json")


def _normalize_company_name(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


@lru_cache
def excluded_company_aliases() -> frozenset[str]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    values = payload.get("excluded_company_aliases", []) if isinstance(payload, dict) else []
    return frozenset(
        _normalize_company_name(str(value))
        for value in values
        if isinstance(value, str) and _normalize_company_name(value)
    )


def is_excluded_company(company_name: str | None) -> bool:
    """头部交易所不进入第三方来源的模型队列，避免浪费复核资源。"""
    if not company_name:
        return False
    return _normalize_company_name(company_name) in excluded_company_aliases()
