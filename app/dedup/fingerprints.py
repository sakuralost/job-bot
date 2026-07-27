import hashlib
import re
import unicodedata


def normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", (value or "").lower())
    return re.sub(r"\s+", " ", value).strip()


def compact_normalized(value: str | None) -> str:
    """忽略大小写、全半角、标点和空白，稳定识别转发时的轻微排版变化。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized(value))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def job_fingerprint(company: str | None, title: str | None, contact: str | None, description: str | None) -> str:
    value = "|".join(normalized(item) for item in (company, title, contact, description))
    return _digest(value)


def job_dedup_keys(
    *,
    message_text: str,
    company: str | None,
    title: str | None,
    contacts: list[str] | tuple[str, ...],
    description: str | None,
) -> list[tuple[str, str]]:
    """生成互补的长期去重键；只使用稳定事实，不依赖数据库行号或扫描批次。"""
    keys: list[tuple[str, str]] = []
    compact_text = compact_normalized(message_text)
    compact_company = compact_normalized(company)
    compact_title = compact_normalized(title)
    compact_contacts = sorted({compact_normalized(value) for value in contacts if compact_normalized(value)})

    if compact_text:
        keys.append(("content", _digest(compact_text)))
    if compact_company and compact_title:
        keys.append(("company_title", _digest(f"{compact_company}|{compact_title}")))
    structured = "|".join(
        (
            compact_company,
            compact_title,
            "|".join(compact_contacts),
            compact_normalized(description),
        )
    )
    if structured.strip("|"):
        keys.append(("structured", _digest(structured)))

    return list(dict.fromkeys(keys))
