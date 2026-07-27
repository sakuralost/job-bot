from __future__ import annotations

import re

_APP_INDICATOR = re.compile(
    r"(?<![a-z])app(?![a-z])|dapp|mobile\s+(?:app|product)|移动端|移动应用|手机应用|客户端产品|"
    r"\bios\b|\bandroid\b",
    re.IGNORECASE,
)
_PRODUCT_ROLE = re.compile(
    r"产品经理|产品负责人|product\s+manager|product\s+owner|product\s+lead",
    re.IGNORECASE,
)


def is_app_product_role(
    *,
    job_title: str | None,
    job_summary: str | None,
    job_description: str | None,
    message_text: str,
) -> bool:
    """识别明确负责 APP、移动端、iOS 或 Android 产品的产品岗位。"""
    structured_text = "\n".join(
        value for value in (job_title, job_summary, job_description) if value
    )
    if _PRODUCT_ROLE.search(structured_text) and _APP_INDICATOR.search(structured_text):
        return True

    # 原消息可能包含多个岗位，只在同一行同时出现产品岗位和 APP 信号时才归类，降低串岗误判。
    for line in message_text.splitlines():
        if _PRODUCT_ROLE.search(line) and _APP_INDICATOR.search(line):
            return True
    return False
