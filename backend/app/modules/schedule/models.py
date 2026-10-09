import uuid
from datetime import date
from decimal import Decimal

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


class ScheduleItem(BusinessBase):
    """Строка ГПР: узел x вид работ x даты x плановый объём (ТЗ §5.2).

    Единица измерения - у вида работ (work.unit). Вес по умолчанию - плановая длительность в днях,
    weight переопределяет его вручную. Правила (живые узел и вид работ, один объект, запрет удалить
    строку со связями или фактом) проверяет БД.
    """

    __tablename__ = "schedule_item"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    node_id: Mapped[uuid.UUID]
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work.id"), index=True)
    plan_start: Mapped[date]
    plan_end: Mapped[date]
    plan_volume: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), default=None)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), default=None, index=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_schedule_item_project_id_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_schedule_item_project_id_project_node",
        ),
        UniqueConstraint("project_id", "id", name="uq_schedule_item_project_id_id"),
        UniqueConstraint(
            "project_id",
            "id",
            "node_id",
            "work_id",
            name="uq_schedule_item_project_id_id_node_id_work_id",
        ),
        CheckConstraint("plan_end >= plan_start", name="dates_ordered"),
        CheckConstraint("plan_volume > 0", name="volume_positive"),
        CheckConstraint("weight IS NULL OR weight > 0", name="weight_positive"),
        Index(
            "uq_schedule_item_node_id_work_id",
            "node_id",
            "work_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_schedule_item_project_id_node_id", "project_id", "node_id"),
    )


class ScheduleDependency(BusinessBase):
    """Зависимость «окончание - начало» (FS): successor начинается после окончания predecessor.

    Обе строки из одного объекта (составные ключи); цикл запрещает триггер БД.
    """

    __tablename__ = "schedule_dependency"

    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    predecessor_id: Mapped[uuid.UUID]
    successor_id: Mapped[uuid.UUID] = mapped_column(index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_schedule_dependency_project_id_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "predecessor_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name="fk_schedule_dependency_predecessor_id_schedule_item",
        ),
        ForeignKeyConstraint(
            ["project_id", "successor_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name="fk_schedule_dependency_successor_id_schedule_item",
        ),
        CheckConstraint("predecessor_id <> successor_id", name="not_self"),
        Index(
            "uq_schedule_dependency_predecessor_id_successor_id",
            "predecessor_id",
            "successor_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
