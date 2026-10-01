import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, LargeBinary, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base

# Обе таблицы доступны только SECURITY DEFINER-функциям (владелец kut_auth): ни у одного логина
# приложения прав на них нет. Модели нужны Alembic (сверка схемы), читать их через ORM нельзя.


class RefreshToken(Base):
    """Непрозрачный refresh-токен: хранится только SHA-256; family_id = id сессии (sid)."""

    __tablename__ = "refresh_token"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    amr: Mapped[list[str]] = mapped_column(ARRAY(String))
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_reason: Mapped[str | None] = mapped_column(default=None)


class RecoveryCode(Base):
    """Одноразовые коды восстановления 2FA: хранятся хэши."""

    __tablename__ = "recovery_code"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        UniqueConstraint("user_id", "code_hash", name="uq_recovery_code_user_id_code_hash"),
        Index("ix_recovery_code_unused", "user_id", postgresql_where=text("used_at IS NULL")),
    )
