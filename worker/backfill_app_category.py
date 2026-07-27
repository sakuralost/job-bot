from __future__ import annotations

from sqlalchemy import select

from app.classifier.product_category import is_app_product_role
from app.database.models import JobPost, TelegramMessage
from app.database.session import SessionLocal


def backfill_app_category() -> dict[str, int]:
    checked = 0
    matched = 0
    with SessionLocal.begin() as db:
        rows = list(
            db.execute(
                select(JobPost, TelegramMessage)
                .join(TelegramMessage, TelegramMessage.id == JobPost.source_message_id)
                .order_by(JobPost.id)
            )
        )
        for job, message in rows:
            job.is_app_product_role = is_app_product_role(
                job_title=job.job_title,
                job_summary=job.job_summary,
                job_description=job.job_description,
                message_text=message.message_text,
            )
            checked += 1
            matched += int(job.is_app_product_role)
    return {"checked": checked, "matched": matched}


if __name__ == "__main__":
    print(f"APP 产品经理分类回填完成：{backfill_app_category()}", flush=True)
