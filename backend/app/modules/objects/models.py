import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class LifecycleStage(StrEnum):
    """Девять стадий жизненного цикла (ТЗ §4.1); «гарантия» вне MVP, модель заложена."""

    LAND_PURCHASE = "land_purchase"
    CONCEPT = "concept"
    DESIGN = "design"
    PERMITS = "permits"
    SITE_PREPARATION = "site_preparation"
    CONSTRUCTION = "construction"
    COMMISSIONING = "commissioning"
    HANDOVER = "handover"
    WARRANTY = "warranty"


class NodeType(StrEnum):
    """Типы узлов дерева: физическая структура; «вид работ» узлом не является (справочник work)."""

    STAGE = "stage"
    BUILDING = "building"
    SECTION = "section"
    FLOOR = "floor"
    ROOM = "room"


# Какие типы допустимы под родителем (None - корень объекта). Те же правила в БД:
# app.project_node_child_allowed(); тест сверяет обе таблицы.
ALLOWED_CHILDREN: dict[NodeType | None, tuple[NodeType, ...]] = {
    None: (NodeType.STAGE, NodeType.BUILDING),
    NodeType.STAGE: (NodeType.BUILDING, NodeType.SECTION),
    NodeType.BUILDING: (NodeType.SECTION, NodeType.FLOOR),
    NodeType.SECTION: (NodeType.FLOOR,),
    NodeType.FLOOR: (NodeType.ROOM,),
    NodeType.ROOM: (),
}

CURRENCY = "KGS"

_STATUSES = ", ".join(f"'{status.value}'" for status in ProjectStatus)
_STAGES = ", ".join(f"'{stage.value}'" for stage in LifecycleStage)
_NODE_TYPES = ", ".join(f"'{kind.value}'" for kind in NodeType)


class Project(BusinessBase):
    """Объект. Бюджет, заказчик, адрес и гарантия лежат в ProjectFinance: их видят только роли с
    правом finance:read (решение PO по S2), и граница стоит в БД, а не только в API."""

    __tablename__ = "project"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    name: Mapped[str]
    status: Mapped[str] = mapped_column(default=ProjectStatus.ACTIVE.value, server_default="active")
    lifecycle_stage: Mapped[str] = mapped_column(
        default=LifecycleStage.LAND_PURCHASE.value,
        server_default=LifecycleStage.LAND_PURCHASE.value,
    )
    planned_start: Mapped[date | None] = mapped_column(Date, default=None)
    planned_end: Mapped[date | None] = mapped_column(Date, default=None)

    __table_args__ = (
        # Цель составного FK потомков (project_id, legal_entity_id), ADR-001 §5.2.
        UniqueConstraint("id", "legal_entity_id", name="uq_project_id_legal_entity_id"),
        CheckConstraint(f"status IN ({_STATUSES})", name="status_valid"),
        CheckConstraint(f"lifecycle_stage IN ({_STAGES})", name="lifecycle_stage_valid"),
        CheckConstraint(
            "planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start",
            name="planned_dates_ordered",
        ),
    )


class ProjectFinance(BusinessBase):
    """Коммерческая часть карточки объекта: бюджет, заказчик, адрес, координаты, гарантия.

    Строки видят роли с правом finance:read (политика RLS по app.has_permission), правят владелец и
    технический директор. Внешнему пулу kut_portal таблица закрыта целиком. Строка появляется при
    первой записи этих полей; нет строки - все поля пусты.
    """

    __tablename__ = "project_finance"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    planned_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    budget_currency: Mapped[str] = mapped_column(default=CURRENCY, server_default=CURRENCY)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counterparty.id"), default=None, index=True
    )
    address: Mapped[str | None] = mapped_column(default=None)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), default=None)
    # Заглушка под гарантийный период (ТЗ §4.3): заполняется после ввода в эксплуатацию.
    warranty_until: Mapped[date | None] = mapped_column(Date, default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_project_finance_project_id_project",
        ),
        UniqueConstraint("project_id", name="uq_project_finance_project_id"),
        CheckConstraint(
            "planned_budget IS NULL OR planned_budget >= 0", name="budget_not_negative"
        ),
        CheckConstraint(f"budget_currency = '{CURRENCY}'", name="budget_currency_kgs"),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name="coordinates_valid",
        ),
    )


class ProjectNode(BusinessBase):
    """Узел дерева объекта (ТЗ §4.2, §5.1): физическая структура, polygon под карту (позже).

    path - идентификаторы от корня до самого узла (uuid[]): его ведёт триггер БД, приложение не
    пишет. Правила дерева (допустимые типы, цикл, живые потомки, один проект) проверяет БД.
    """

    __tablename__ = "project_node"

    project_id: Mapped[uuid.UUID]
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    type: Mapped[str]
    name: Mapped[str]
    code: Mapped[str | None] = mapped_column(default=None)
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    path: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'::uuid[]")
    )
    foreman_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), default=None, index=True
    )
    master_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), default=None, index=True
    )
    planned_start: Mapped[date | None] = mapped_column(Date, default=None)
    planned_end: Mapped[date | None] = mapped_column(Date, default=None)
    # none_as_null: Python None - это SQL NULL, а не JSON-значение null (его не пускает CHECK).
    polygon: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), default=None)

    __table_args__ = (
        # Юрлицо узла всегда равно юрлицу объекта (ADR-001 §5.2); сам объект его не меняет.
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_project_node_project_id_project",
        ),
        # Родитель из того же объекта; у корня parent_id пуст, и проверка не срабатывает.
        ForeignKeyConstraint(
            ["project_id", "parent_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_project_node_project_id_project_node",
        ),
        UniqueConstraint("project_id", "id", name="uq_project_node_project_id_id"),
        CheckConstraint(f"type IN ({_NODE_TYPES})", name="type_valid"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("code IS NULL OR btrim(code) <> ''", name="code_not_blank"),
        CheckConstraint(
            "planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start",
            name="planned_dates_ordered",
        ),
        CheckConstraint("app.is_valid_polygon(polygon)", name="polygon_valid"),
        Index(
            "ix_project_node_project_id_parent_id_sort_order",
            "project_id",
            "parent_id",
            "sort_order",
        ),
        Index("ix_project_node_path", "path", postgresql_using="gin"),
        Index(
            "uq_project_node_root_code",
            "project_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND code IS NOT NULL AND parent_id IS NULL"),
        ),
        Index(
            "uq_project_node_sibling_code",
            "project_id",
            "parent_id",
            "code",
            unique=True,
            postgresql_where=text(
                "deleted_at IS NULL AND code IS NOT NULL AND parent_id IS NOT NULL"
            ),
        ),
    )
