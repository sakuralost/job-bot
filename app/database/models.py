from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MonitoredChat(Timestamped, Base):
    __tablename__ = "monitored_chats"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scanned_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobDedupGroup(Timestamped, Base):
    """跨扫描长期保留的岗位身份，以及用户对这一岗位做出的人工决定。"""

    __tablename__ = "job_dedup_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobDedupKey(Base):
    """一个岗位可拥有原文、公司岗位、联系方式岗位等多种稳定去重键。"""

    __tablename__ = "job_dedup_keys"
    __table_args__ = (UniqueConstraint("key_type", "key_hash", name="uq_job_dedup_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("job_dedup_groups.id"), index=True)
    key_type: Mapped[str] = mapped_column(String(40))
    key_hash: Mapped[str] = mapped_column(String(64))


class TelegramMessage(Timestamped, Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (UniqueConstraint("telegram_chat_id", "telegram_message_id", name="uq_telegram_message"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    sender_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, default="")
    message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    grouped_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="collected", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classification_prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedup_group_id: Mapped[int | None] = mapped_column(ForeignKey("job_dedup_groups.id"), nullable=True, index=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duplicate_of_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_messages.id"), nullable=True, index=True
    )
    classification_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class WebJobListing(Timestamped, Base):
    """招聘网站的原始职位；与 Telegram 消息分开保存，避免伪造消息编号。"""

    __tablename__ = "web_job_listings"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_web_job_listing_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(500))
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending_classification", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    classification_prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classification_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    dedup_group_id: Mapped[int | None] = mapped_column(ForeignKey("job_dedup_groups.id"), nullable=True, index=True)

class WebSource(Timestamped, Base):
    """可在仪表盘屏蔽或恢复的第三方招聘网站来源。"""

    __tablename__ = "web_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    website_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    lookback_days: Mapped[int] = mapped_column(Integer, default=7)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class JobPost(Timestamped, Base):
    __tablename__ = "job_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_messages.id"), unique=True, nullable=True)
    source_web_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_job_listings.id"), unique=True, nullable=True
    )
    job_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_mode: Mapped[str] = mapped_column(String(20), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    english_speaking_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    chinese_friendly_level: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    chinese_friendly_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_product_role: Mapped[bool] = mapped_column(Boolean, default=False)
    is_app_product_role: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    source_message_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    dedup_group_id: Mapped[int | None] = mapped_column(ForeignKey("job_dedup_groups.id"), nullable=True, index=True)
    duplicate_of_job_post_id: Mapped[int | None] = mapped_column(ForeignKey("job_posts.id"), nullable=True, index=True)


class Contact(Timestamped, Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    discord: Mapped[str | None] = mapped_column(String(255), nullable=True)
    other_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_identity: Mapped[str] = mapped_column(String(255), index=True)
    is_contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobContact(Base):
    __tablename__ = "job_contacts"
    job_post_id: Mapped[int] = mapped_column(ForeignKey("job_posts.id"), primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), primary_key=True)
    contact_type: Mapped[str] = mapped_column(String(40))
    contact_value: Mapped[str] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="running")
    total_chats: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    rule_candidates: Mapped[int] = mapped_column(Integer, default=0)
    llm_candidates: Mapped[int] = mapped_column(Integer, default=0)
    accepted_jobs: Mapped[int] = mapped_column(Integer, default=0)
    excluded_jobs: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_jobs: Mapped[int] = mapped_column(Integer, default=0)
    contacted_filtered: Mapped[int] = mapped_column(Integer, default=0)
    notified_jobs: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class LlmPromptVersion(Base):
    __tablename__ = "llm_prompt_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    system_prompt: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationRecord(Base):
    __tablename__ = "notification_records"
    __table_args__ = (UniqueConstraint("job_post_id", "notification_type", name="uq_notification_per_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_post_id: Mapped[int] = mapped_column(ForeignKey("job_posts.id"))
    telegram_bot_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_bot_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notification_type: Mapped[str] = mapped_column(String(40))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReclassificationRun(Base):
    __tablename__ = "reclassification_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_version: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    accepted_items: Mapped[int] = mapped_column(Integer, default=0)
    excluded_items: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_items: Mapped[int] = mapped_column(Integer, default=0)
    error_items: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReclassificationItem(Base):
    __tablename__ = "reclassification_items"
    __table_args__ = (UniqueConstraint("run_id", "message_id", name="uq_reclassification_run_message"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reclassification_runs.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id"), index=True)
    job_post_id: Mapped[int] = mapped_column(ForeignKey("job_posts.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManualScanRequest(Base):
    """管理员主动发起的增量扫描及其后续 LLM 筛选状态。"""

    __tablename__ = "manual_scan_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    trigger_source: Mapped[str] = mapped_column(String(20), default="manual")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    llm_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True, index=True)
    messages_found: Mapped[int] = mapped_column(Integer, default=0)
    rule_candidates: Mapped[int] = mapped_column(Integer, default=0)
    scan_error_count: Mapped[int] = mapped_column(Integer, default=0)
    classification_total: Mapped[int] = mapped_column(Integer, default=0)
    classification_processed: Mapped[int] = mapped_column(Integer, default=0)
    accepted_items: Mapped[int] = mapped_column(Integer, default=0)
    excluded_items: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_items: Mapped[int] = mapped_column(Integer, default=0)
    error_items: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanSchedule(Base):
    """后台可编辑的每日自动检测时间。"""

    __tablename__ = "scan_schedules"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_times: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["04:00"])
    last_triggered_slot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
