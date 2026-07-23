import re
from app.classifier.schemas import ExtractedContact
from app.extractor.normalizer import email, phone, telegram_username

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TELEGRAM = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})|(?<!\w)@([A-Za-z0-9_]{5,})")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\- ()]{6,}\d)(?!\w)")


def extract(text: str) -> list[ExtractedContact]:
    result: list[ExtractedContact] = []
    for match in TELEGRAM.finditer(text):
        result.append(ExtractedContact(contact_type="telegram_username", value=telegram_username(match.group(1) or match.group(2))))
    for match in EMAIL.finditer(text):
        result.append(ExtractedContact(contact_type="email", value=email(match.group())))
    for match in PHONE.finditer(text):
        result.append(ExtractedContact(contact_type="phone", value=phone(match.group())))
    return result
