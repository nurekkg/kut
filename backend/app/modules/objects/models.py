import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ARCHIVED = "archived"


_STATUSES = ", ".join(f"'{status.value}'" for status in ProjectStatus)


class Project(BusinessBase):
    """Минимальный объект: S2 добавит стадию, даты, ответственных и бюджет (ТЗ §5.1)."""

    __tablename__ = "project"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    name: Mapped[str]
    status: Mapped[str] = mapped_column(default=ProjectStatus.ACTIVE.value, server_default="active")

    __table_args__ = (
        # Цель составного FK потомков (project_id, legal_entity_id), ADR-001 §5.2.
        UniqueConstraint("id", "legal_entity_id", name="uq_project_id_legal_entity_id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status_valid"),
    )
