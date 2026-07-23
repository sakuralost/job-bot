import hashlib
import re


def normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def job_fingerprint(company: str | None, title: str | None, contact: str | None, description: str | None) -> str:
    value = "|".join(normalized(item) for item in (company, title, contact, description))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
