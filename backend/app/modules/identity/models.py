import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class User(BusinessBase):
    __tablename__ = "user"

    phone: Mapped[str | None] = mapped_column(default=None)
    email: Mapped[str | None] = mapped_column(default=None)
    full_name: Mapped[str]
    # Секреты закрыты колонковыми правами (kut_app их не читает и не пишет: только функции app.*);
    # deferred: обычный select(User) не просит их у БД.
    password_hash: Mapped[str | None] = mapped_column(default=None, deferred=True)
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, default=None, deferred=True)
    totp_key_id: Mapped[str | None] = mapped_column(default=None, deferred=True)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(default=None)
    password_changed_at: Mapped[datetime | None] = mapped_column(default=None)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id"), index=True)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counterparty.id"), default=None, index=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    role_ver: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    # WhatsApp только по согласию: по умолчанию выключено. Даты ставит гард user_notify_guard.
    notify_whatsapp: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    notify_whatsapp_consent_at: Mapped[datetime | None] = mapped_column(default=None)
    notify_whatsapp_revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    notify_whatsapp_consent_version: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        CheckConstraint("phone IS NOT NULL OR email IS NOT NULL", name="contact_required"),
        CheckConstraint(r"phone ~ '^\+[1-9][0-9]{6,14}$'", name="phone_e164"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        Index("uq_user_email", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("uq_user_phone", "phone", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )


class UserProjectAccess(BusinessBase):
    """role_id задан: роль на объекте переопределена (вариант B, ADR-001 §3); обычно NULL."""

    __tablename__ = "user_project_access"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), index=True)
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("role.id"), default=None, index=True
    )

    __table_args__ = (
        Index(
            "uq_user_project_access_user_id_project_id",
            "user_id",
            "project_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class UserLegalEntityAccess(BusinessBase):
    """Доступ к юрлицу без объекта: накладные и платежи с project_id IS NULL (ADR-001 §5.2)."""

    __tablename__ = "user_legal_entity_access"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)

    __table_args__ = (
        Index(
            "uq_user_legal_entity_access_user_id_legal_entity_id",
            "user_id",
            "legal_entity_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
