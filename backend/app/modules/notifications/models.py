import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class NotificationTemplate(BusinessBase):
    """Шаблон сообщения: имя в WhatsApp, статус одобрения провайдером, превью текста."""

    __tablename__ = "notification_template"

    code: Mapped[str] = mapped_column(String(60))
    channel: Mapped[str] = mapped_column(String(20))
    wa_name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(10))
    approval: Mapped[str] = mapped_column(String(20))
    preview: Mapped[str] = mapped_column(Text())
    params: Mapped[str] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("code", name="uq_notification_template_code"),
        CheckConstraint("channel IN ('whatsapp')", name="channel"),
        CheckConstraint("approval IN ('approved', 'pending')", name="approval"),
    )


class NotificationOutbox(BusinessBase):
    """Очередь уведомлений: строка пишется в транзакции события (см. ADR-015 часть 6, раздел 5)."""

    __tablename__ = "notification_outbox"

    event_type: Mapped[str] = mapped_column(String(40))
    dedup_key: Mapped[str] = mapped_column(String(200))
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), default=None)
    template_code: Mapped[str] = mapped_column(ForeignKey("notification_template.code"))
    channel: Mapped[str] = mapped_column(String(20), server_default=text("'whatsapp'"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB(), server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(60), default=None)

    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_notification_outbox_dedup_key"),
        CheckConstraint(
            "event_type IN ('approval_requested', 'approval_approved', 'approval_rejected', "
            "'delegation_assigned')",
            name="event_type",
        ),
        CheckConstraint("status IN ('queued', 'sent', 'failed', 'dead')", name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index(
            "ix_notification_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )


class NotificationDelivery(BusinessBase):
    """Журнал попыток отправки (APPEND_ONLY): телефон только маской."""

    __tablename__ = "notification_delivery"

    outbox_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notification_outbox.id"))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    provider_status: Mapped[int | None] = mapped_column(Integer, default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(120), default=None)
    error_code: Mapped[str | None] = mapped_column(String(60), default=None)
    phone_masked: Mapped[str] = mapped_column(String(32))
    defer_until: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint("status IN ('logged', 'sent', 'retry', 'failed', 'dead')", name="status"),
        CheckConstraint("attempt >= 1", name="attempt"),
    )
