import re


def telegram_username(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^(https?://)?t\.me/", "", value).lstrip("@")
    return value.split("?", 1)[0].strip("/")


def email(value: str) -> str:
    return value.strip().lower()


def phone(value: str) -> str:
    return re.sub(r"[\s()\-]", "", value.strip())


def wechat(value: str) -> str:
    return re.sub(r"^(微信|vx|wechat)\s*[:：]?\s*", "", value.strip(), flags=re.I).lower()
