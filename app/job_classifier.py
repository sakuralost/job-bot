from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import request


@dataclass(frozen=True)
class Classification:
    is_match: bool
    confidence: float
    reason: str
    matched_excerpt: str


SYSTEM_PROMPT = """你是 Telegram 中文招聘信息的严格筛选器。用户只找产品经理相关岗位。
目标职位：产品经理、高级产品经理、产品负责人、产品总监、产品专家、product manager、product owner、product lead、senior product manager、web3 pm、wallet product manager、exchange product manager。
排除博彩、赌博、赌场、彩票、体育投注、成人色情、escort、包网、白标博彩、资金盘、跑分、杀猪盘和灰产。

一条消息经常同时招聘多个岗位。必须先按每个岗位条目分别理解：只有存在目标职位时 is_match 才为 true；不要因为 Flutter、开发、运维、设计等其他岗位提高产品岗位的匹配度。若命中，matched_excerpt 只能保留该产品岗位的原文段落及紧邻薪资/要求/联系信息，不能保留其他岗位。若没有产品岗位，matched_excerpt 为空。不要推测消息未明确提供的信息。
只返回 JSON：{
  "is_match": boolean,
  "confidence": 0 到 1 的数字,
  "reason": "不超过50字的中文理由",
  "matched_excerpt": "命中时的原文摘录，否则空字符串"
}"""


def classify(text: str) -> Classification:
    endpoint = os.getenv("JOB_BOT_LLM_URL", "http://127.0.0.1:11435/api/generate")
    model = os.getenv("JOB_BOT_LLM_MODEL", "qwen2.5:3b")
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": text[:9000],
        "format": "json",
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 420},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=120) as response:  # noqa: S310 - local-only endpoint from config
        raw = json.loads(response.read().decode("utf-8"))
    parsed = json.loads(raw["response"])
    matched = bool(parsed.get("is_match"))
    confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0))))
    reason = str(parsed.get("reason", "LLM 未给出理由"))[:200]
    excerpt = str(parsed.get("matched_excerpt", "")).strip()[:9000] if matched else ""
    if matched and not excerpt:
        raise ValueError("LLM 命中但没有返回岗位摘录")
    return Classification(matched, confidence, reason, excerpt)
