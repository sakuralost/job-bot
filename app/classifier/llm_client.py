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


def _escape_control_characters_in_strings(content: str) -> str:
    """修复本地模型偶尔在 JSON 字符串内部输出的未转义换行和控制字符。"""
    result: list[str] = []
    in_string = False
    escaped = False
    replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for character in content:
        if in_string:
            if escaped:
                escaped = False
                result.append(character)
                continue
            if character == "\\":
                escaped = True
                result.append(character)
                continue
            if character == '"':
                in_string = False
                result.append(character)
                continue
            if ord(character) < 32:
                result.append(replacements.get(character, " "))
                continue
        elif character == '"':
            in_string = True
        result.append(character)
    return "".join(result)


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
        timeout=120,  # Qwen3.6-27B 是推理模型，先思考再作答，简单问题实测也要 40~90 秒
    )
    content = response.choices[0].message.content or "{}"
    cleaned = _escape_control_characters_in_strings(_strip_code_fence(content))
    return JobClassificationResult.model_validate_json(cleaned)
