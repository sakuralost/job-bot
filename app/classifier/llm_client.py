from __future__ import annotations

import re

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from app.classifier.schemas import JobClassificationResult
from app.config import settings
from app.services.prompt_service import DEFAULT_SYSTEM_PROMPT


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMInfrastructureError(RuntimeError):
    """模型服务连接、超时或 5xx 故障；批任务必须立即停下，不能继续撞击显卡。"""



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
    # OpenAI SDK 默认会暗中重试两次。这里关闭隐藏重试，避免一次 CUDA 故障被放大成连续三次请求。
    client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url, max_retries=0)
    try:
        kwargs = {
            "model": cfg.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "请判断以下 Telegram 招聘消息，只依据原文返回规定的 JSON：\n\n" + text[:12000],
                },
            ],
            "timeout": 180,
        }
        # 本地 Qwen 需要 extra_body；云端 OpenAI 兼容模型不接受 chat_template_kwargs
        base = cfg.openai_base_url or ""
        if "127.0.0.1" in base or "11434" in base:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        response = client.chat.completions.create(**kwargs)
    except (APIConnectionError, APITimeoutError) as exc:
        raise LLMInfrastructureError("LLM 连接或响应超时；批任务已停止，消息保留待重试") from exc
    except APIStatusError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            raise LLMInfrastructureError(
                f"LLM 服务返回 {exc.status_code}；批任务已停止，消息保留待重试"
            ) from exc
        raise
    content = response.choices[0].message.content or "{}"
    cleaned = _escape_control_characters_in_strings(_strip_code_fence(content))
    return JobClassificationResult.model_validate_json(cleaned)
