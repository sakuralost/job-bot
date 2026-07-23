from __future__ import annotations

import re

from openai import OpenAI
from app.classifier.schemas import JobClassificationResult
from app.config import settings


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_code_fence(content: str) -> str:
    """本地模型不总是严格遵守 response_format=json_object，经常把 JSON 包在 ```json ``` 代码块里。"""
    return _JSON_FENCE.sub("", content.strip())


SYSTEM_PROMPT = """You classify Telegram job posts for a candidate who only wants Product Manager roles.
Use only information present in the message; never invent facts.

Multi-job posts: many messages advertise several unrelated roles in one post (e.g. Flutter engineer,
ops engineer, product manager). Judge is_product_role and confidence based ONLY on the product-manager
paragraph. The presence of other roles (engineer, ops, designer, sales) in the SAME message must NOT
raise confidence or is_product_role — if the message has no product-manager paragraph at all, set
is_product_role=false even if it is clearly a real job post for something else.

Product manager roles are allowed (including product owner, product lead); project managers, operations
managers, sales/account managers, and community managers are NOT product roles.

work_mode/is_remote must reflect what the message actually states: "remote" only if the message says
remote/远程/线上办公 or similar; "hybrid" or "onsite" if it requires being in a specific city/office;
"unknown" if the message does not say. Do not guess remote when unstated.

Gambling, adult content, white-label gambling, fund-running (跑分), pig-butchering scams, money laundering
and other grey-market roles are excluded regardless of job title.

Return ONLY a single JSON object (no markdown code fence, no commentary) with exactly these keys:
is_job_post (bool), is_product_role (bool), role_match_reason (string or null), company_name (string or
null), job_title (string or null), industry (string or null), is_excluded (bool), excluded_category
(string or null), excluded_reason (string or null), work_mode ("remote"|"hybrid"|"onsite"|"unknown"),
is_remote (bool or null), location (string or null), salary (string or null), language_requirement
(string or null), english_speaking_required (bool or null), experience_requirement (string or null),
job_description (string or null), job_summary (string or null),
contacts (array of {contact_type, value, is_primary}), confidence (0-1 float), reasoning_summary (string).
Do not reveal chain-of-thought; reasoning_summary must be a short factual justification only."""


def classify(text: str) -> JobClassificationResult:
    cfg = settings()
    if not cfg.openai_api_key or not cfg.openai_model:
        raise RuntimeError("LLM 未配置；消息会保留为待重试，不会丢失")
    client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)
    response = client.chat.completions.create(
        model=cfg.openai_model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text[:12000]}],
        timeout=120,  # Qwen3.6-27B 是推理模型，先思考再作答，简单问题实测也要 40~90 秒
    )
    content = response.choices[0].message.content or "{}"
    return JobClassificationResult.model_validate_json(_strip_code_fence(content))
