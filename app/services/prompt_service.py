from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update

from app.database.models import LlmPromptVersion
from app.database.session import SessionLocal


DEFAULT_SYSTEM_PROMPT = """你是一个严格的 Telegram 招聘信息筛选器。候选人只考虑产品经理相关岗位，并优先考虑明确支持远程工作的机会。你只能依据消息原文判断，禁止补充或猜测原文没有提供的信息。

请遵守以下判断规则：

1. 一段职位原文可能同时发布多个岗位。判断 is_product_role 和 confidence 时，只能依据产品经理岗位所在的段落；开发、运维、设计、销售等其他岗位不能提高产品岗位的匹配度。如果原文中没有产品经理岗位段落，即使它确实在招聘，也必须设置 is_product_role=false。
2. 产品经理、Product Manager、Product Owner、Product Lead、产品负责人等属于目标岗位。项目经理、运营经理、销售经理、客户经理、社区经理等不属于产品经理岗位。
3. 候选人优先寻找远程产品经理岗位。只有原文明示“远程”“remote”“线上办公”“居家办公”等含义时，才能设置 is_remote=true 和 work_mode="remote"。要求到办公室或固定地点办公时使用 "onsite"；明确混合办公时使用 "hybrid"；没有说明时使用 "unknown"，禁止猜测为远程。
4. 博彩、赌博、成人内容、色情、博彩白标、资金盘、跑分、杀猪盘、洗钱、诈骗和其他灰色产业，无论岗位名称是什么，都必须设置 is_excluded=true。
5. 公司、岗位、行业、地点、薪资、经验要求、语言要求和联系方式只能从原文提取。没有提供的信息必须返回 null 或空数组。
6. role_match_reason、excluded_reason、job_summary、job_description 和 reasoning_summary 等解释性文字必须使用简洁中文。公司名、岗位原名和联系方式可以保留原文语言。
7. chinese_friendly_level 仅用于判断中文母语、中文工作环境或英语能力较弱的候选人是否值得查看：原文明确要求中文、普通话、华语或中文工作环境时为 explicit_chinese；原文明确没有英语要求或明确中文即可工作时为 no_english_requirement；原文明确要求英文流利、英文可作为工作语言、商务英语、母语级英语或英文读写听说时为 english_required，并设置 english_speaking_required=true；其余没有明确语言信息的情况必须为 needs_confirmation，不能因为职位在香港、新加坡、远程或原文是英文就猜测。chinese_friendly_reason 必须用简洁中文说明原文证据。

只能返回一个 JSON 对象，不能使用 Markdown 代码块，不能添加 JSON 以外的解释。JSON 必须包含以下字段：

is_job_post（布尔值）；
is_product_role（布尔值）；
role_match_reason（中文字符串或 null）；
company_name（字符串或 null）；
job_title（字符串或 null）；
industry（字符串或 null）；
is_excluded（布尔值）；
excluded_category（字符串或 null）；
excluded_reason（中文字符串或 null）；
work_mode（只能是 "remote"、"hybrid"、"onsite" 或 "unknown"）；
is_remote（布尔值或 null）；
location（字符串或 null）；
salary（字符串或 null）；
language_requirement（字符串或 null）；
english_speaking_required（布尔值或 null）；
chinese_friendly_level（只能是 "explicit_chinese"、"no_english_requirement"、"needs_confirmation" 或 "english_required"）；
chinese_friendly_reason（中文字符串或 null）；
experience_requirement（字符串或 null）；
job_description（中文字符串或 null）；
job_summary（中文字符串或 null）；
contacts（数组，每项包含 contact_type、value、is_primary）；
confidence（0 到 1 的数字）；
reasoning_summary（简短中文事实理由）。

不要输出思维链或内部推理过程。reasoning_summary 只能给出简短、可核对的中文判断依据。"""

REQUIRED_OUTPUT_FIELDS = {
    "is_job_post",
    "is_product_role",
    "is_excluded",
    "work_mode",
    "is_remote",
    "chinese_friendly_level",
    "contacts",
    "confidence",
    "reasoning_summary",
}
MIN_PROMPT_LENGTH = 200
MAX_PROMPT_LENGTH = 20_000
MIN_CHINESE_CHARACTERS = 50


@dataclass(frozen=True)
class PromptSnapshot:
    id: int | None
    version: int
    system_prompt: str
    change_note: str | None
    created_at: datetime | None
    is_active: bool = True
    is_default: bool = False


def validate_system_prompt(value: str) -> str:
    prompt = value.strip()
    if len(prompt) < MIN_PROMPT_LENGTH:
        raise ValueError(f"提示词至少需要 {MIN_PROMPT_LENGTH} 个字符，避免筛选条件过于模糊")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError(f"提示词不能超过 {MAX_PROMPT_LENGTH} 个字符")
    chinese_count = sum("\u4e00" <= character <= "\u9fff" for character in prompt)
    if chinese_count < MIN_CHINESE_CHARACTERS:
        raise ValueError(f"提示词主体必须使用中文，至少需要 {MIN_CHINESE_CHARACTERS} 个中文字符")
    missing = sorted(field for field in REQUIRED_OUTPUT_FIELDS if field not in prompt)
    if missing:
        raise ValueError("提示词缺少模型输出字段：" + "、".join(missing))
    return prompt


def _snapshot(row: LlmPromptVersion) -> PromptSnapshot:
    return PromptSnapshot(
        id=row.id,
        version=row.version,
        system_prompt=row.system_prompt,
        change_note=row.change_note,
        created_at=row.created_at,
        is_active=row.is_active,
    )


def get_active_prompt() -> PromptSnapshot:
    with SessionLocal() as db:
        row = db.scalar(
            select(LlmPromptVersion)
            .where(LlmPromptVersion.is_active.is_(True))
            .order_by(LlmPromptVersion.version.desc())
            .limit(1)
        )
        if row is not None:
            return _snapshot(row)
    return PromptSnapshot(
        id=None,
        version=0,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        change_note="系统内置默认提示词",
        created_at=None,
        is_active=True,
        is_default=True,
    )


def prompt_history(limit: int = 8) -> list[PromptSnapshot]:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(LlmPromptVersion)
                .order_by(LlmPromptVersion.version.desc())
                .limit(max(1, min(limit, 50)))
            )
        )
        return [_snapshot(row) for row in rows]


def save_prompt(system_prompt: str, change_note: str | None = None) -> PromptSnapshot:
    prompt = validate_system_prompt(system_prompt)
    note = (change_note or "").strip()[:500] or None
    with SessionLocal.begin() as db:
        latest_version = db.scalar(select(func.max(LlmPromptVersion.version))) or 0
        db.execute(update(LlmPromptVersion).values(is_active=False))
        row = LlmPromptVersion(
            version=latest_version + 1,
            system_prompt=prompt,
            change_note=note,
            is_active=True,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return _snapshot(row)


def activate_prompt_version(prompt_id: int) -> PromptSnapshot:
    with SessionLocal.begin() as db:
        row = db.get(LlmPromptVersion, prompt_id)
        if row is None:
            raise LookupError("未找到该提示词版本")
        db.execute(update(LlmPromptVersion).values(is_active=False))
        row.is_active = True
        db.flush()
        db.refresh(row)
        return _snapshot(row)
