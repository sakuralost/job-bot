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


class JobPost(Timestamped, Base):
    __tablename__ = "job_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("telegram_messages.id"), unique=True)
    job_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_mode: Mapped[str] = mapped_column(String(20), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_product_role: Mapped[bool] = mapped_column(Boolean, default=False)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    source_message_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)


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
