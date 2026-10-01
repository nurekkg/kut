import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, LargeBinary, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class IdempotencyKey(Base):
    """Результат выполненной мутации (ADR-001 §5.1); пишется в одной транзакции с изменениями."""

    __tablename__ = "idempotency_key"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    response_code: Mapped[int]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    stored_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(index=True)
