import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class ActualStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


_STATUSES = ", ".join(f"'{status.value}'" for status in ActualStatus)


class WorkActual(BusinessBase):
    """Факт работ (ТЗ §5.3): узел x вид работ x объём x дата x автор, с геометкой и фото.

    Статусы draft -> submitted -> approved (или rejected с причиной); в % готовности идёт только
    approved. Переходы и права на них проверяет триггер БД app.work_actual_guard: автор,
    подтвердивший и время вычисляются там, приложение их не пишет. Факт без строки ГПР разрешён
    (schedule_item_id пуст), строка находится автоматически, если для узла и вида работ она есть.
    """

    __tablename__ = "work_actual"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    node_id: Mapped[uuid.UUID]
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work.id"), index=True)
    schedule_item_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    work_date: Mapped[date]
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    status: Mapped[str] = mapped_column(
        default=ActualStatus.DRAFT.value, server_default=ActualStatus.DRAFT.value
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    reject_reason: Mapped[str | None] = mapped_column(default=None)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_work_actual_project_id_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_work_actual_project_id_project_node",
        ),
        # Факт, привязанный к строке ГПР, лежит в том же объекте на том же узле и по тому же виду
        # работ; у факта без плана schedule_item_id пуст, и проверка не срабатывает.
        ForeignKeyConstraint(
            ["project_id", "schedule_item_id", "node_id", "work_id"],
            [
                "schedule_item.project_id",
                "schedule_item.id",
                "schedule_item.node_id",
                "schedule_item.work_id",
            ],
            name="fk_work_actual_project_id_schedule_item",
        ),
        UniqueConstraint("project_id", "id", name="uq_work_actual_project_id_id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status_valid"),
        CheckConstraint("volume > 0", name="volume_positive"),
        CheckConstraint(
            "(status = 'approved') = (approved_by IS NOT NULL)"
            " AND (approved_by IS NULL) = (approved_at IS NULL)",
            name="approval_consistent",
        ),
        CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)"
            " AND (reject_reason IS NULL OR btrim(reject_reason) <> '')",
            name="rejection_consistent",
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name="coordinates_valid",
        ),
        CheckConstraint(
            "accuracy_m IS NULL OR (latitude IS NOT NULL AND accuracy_m >= 0"
            " AND accuracy_m <= 100000)",
            name="accuracy_valid",
        ),
        Index("ix_work_actual_node_id_work_date", "node_id", "work_date"),
        Index(
            "ix_work_actual_schedule_item_id",
            "schedule_item_id",
            postgresql_where=text("schedule_item_id IS NOT NULL"),
        ),
        Index("ix_work_actual_project_id_status", "project_id", "status"),
    )
