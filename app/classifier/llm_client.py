from __future__ import annotations

import re

from openai import OpenAI
from app.classifier.schemas import JobClassificationResult
from app.config import settings
from app.services.prompt_service import DEFAULT_SYSTEM_PROMPT


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_code_fence(content: str) -> str:
    """本地模型不总是严格遵守 response_format=json_object，经常把 JSON 包在 ```json ``` 代码块里。"""
    return _JSON_FENCE.sub("", content.strip())


def classify(text: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> JobClassificationResult:
    cfg = settings()
    if not cfg.openai_api_key or not cfg.openai_model:
        raise RuntimeError("LLM 未配置；消息会保留为待重试，不会丢失")
    client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)
    response = client.chat.completions.create(
        model=cfg.openai_model,
        temperature=0,
        reasoning_effort="none",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "请判断以下 Telegram 招聘消息，只依据原文返回规定的 JSON：\n\n" + text[:12000],
            },
        ],
        timeout=120,  # 给本地推理模型保留足够响应时间
    )
    content = response.choices[0].message.content or "{}"
    return JobClassificationResult.model_validate_json(_strip_code_fence(content))
