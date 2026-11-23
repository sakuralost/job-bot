from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator

_CONTACT_TYPE_ALIASES = {
    "telegram": "telegram_username", "tg": "telegram_username", "telegram_handle": "telegram_username",
    "username": "telegram_username", "user_id": "telegram_user_id", "id": "telegram_user_id",
    "mail": "email", "e-mail": "email", "tel": "phone", "mobile": "phone", "whatsapp": "other",
}


class ExtractedContact(BaseModel):
    contact_type: Literal["telegram_user_id", "telegram_username", "email", "phone", "wechat", "discord", "url", "sender", "other"]
    value: str
    is_primary: bool = False

    @field_validator("contact_type", mode="before")
    @classmethod
    def _normalize_contact_type(cls, value: object) -> object:
        # 本地模型不总是严格按 schema 里列出的字面量返回，先归一化常见变体，
        # 归一化不了也别让整条消息因为一个联系方式字段直接判定失败。
        if isinstance(value, str):
            key = value.strip().lower()
            return _CONTACT_TYPE_ALIASES.get(key, key if key in {"telegram_user_id", "telegram_username", "email", "phone", "wechat", "discord", "url", "sender", "other"} else "other")
        return value


_WORK_MODE_ALIASES = {
    "远程": "remote", "远程办公": "remote", "线上办公": "remote", "居家办公": "remote", "wfh": "remote",
    "混合": "hybrid", "混合办公": "hybrid", "现场": "onsite", "驻场": "onsite", "到岗": "onsite", "坐班": "onsite",
    "未知": "unknown", "未说明": "unknown",
}


class JobClassificationResult(BaseModel):
    is_job_post: bool
    is_product_role: bool
    role_match_reason: str | None = None
    company_name: str | None = None
    job_title: str | None = None
    industry: str | None = None
    is_excluded: bool
    excluded_category: str | None = None
    excluded_reason: str | None = None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    is_remote: bool | None = None

    @field_validator("work_mode", mode="before")
    @classmethod
    def _normalize_work_mode(cls, value: object) -> object:
        if isinstance(value, str):
            key = value.strip().lower()
            return _WORK_MODE_ALIASES.get(key, key if key in {"remote", "hybrid", "onsite", "unknown"} else "unknown")
        return value
    location: str | None = None
    salary: str | None = None
    language_requirement: str | None = None
    english_speaking_required: bool | None = None
    chinese_friendly_level: Literal[
        "explicit_chinese", "no_english_requirement", "needs_confirmation", "english_required"
    ] = "needs_confirmation"
    chinese_friendly_reason: str | None = None
    experience_requirement: str | None = None
    job_description: str | None = None
    job_summary: str | None = None
    contacts: list[ExtractedContact] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasoning_summary: str
