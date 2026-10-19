import uuid
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, BusinessBase
from app.modules.documents.types import (
    CONTRACT_KINDS,
    COUNTERPARTY_KINDS,
    EXTENSIONS,
    FINANCIAL_KINDS,
    KINDS,
    LEGAL_KINDS,
    MAX_FILE_BYTES,
    OBJECT_KINDS,
    VERSION_STATUSES,
)


def _in(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


_KIND_VALUES = _in(KINDS)
_FINANCIAL = _in(FINANCIAL_KINDS)
_OBJECT = _in(OBJECT_KINDS)
_LEGAL = _in(LEGAL_KINDS)
_COUNTERPARTY = _in(COUNTERPARTY_KINDS)
_CONTRACT = _in(CONTRACT_KINDS)
_STATUSES = _in(VERSION_STATUSES)
_CONTENT_TYPES = _in(EXTENSIONS)


class Document(BusinessBase):
    """Шапка документа (ADR-015 часть 1, раздел 3): вид, юрлицо, объект или юрлицо-область,
    контрагент, договор, регистрационный номер, текущая версия. Документ не удаляется: снятие с
    действия - архивная дата (`archived_at`)."""

    __tablename__ = "document"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None, index=True)
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counterparty.id"), default=None, index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contract.id"), default=None, index=True
    )
    kind: Mapped[str]
    financial: Mapped[bool]
    number_year: Mapped[int] = mapped_column(Integer)
    number_seq: Mapped[int] = mapped_column(Integer)
    registration_no: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_version.id", use_alter=True), default=None
    )
    archived_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_project_id_project",
        ),
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_valid"),
        CheckConstraint(f"financial = (kind IN ({_FINANCIAL}))", name="financial_matches_kind"),
        CheckConstraint(
            f"CASE WHEN kind IN ({_OBJECT}) THEN project_id IS NOT NULL"
            f" WHEN kind IN ({_LEGAL}) THEN project_id IS NULL ELSE true END",
            name="scope_valid",
        ),
        CheckConstraint(
            f"CASE WHEN kind IN ({_COUNTERPARTY}) THEN counterparty_id IS NOT NULL ELSE true END",
            name="counterparty_required",
        ),
        CheckConstraint(
            f"CASE WHEN kind IN ({_CONTRACT}) THEN contract_id IS NOT NULL ELSE true END",
            name="contract_required",
        ),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("number_seq >= 1 AND number_year >= 2000", name="number_valid"),
        Index(
            "uq_document_legal_entity_id_registration_no",
            "legal_entity_id",
            "registration_no",
            unique=True,
        ),
    )


class DocumentVersion(BusinessBase):
    """Версия файла (ADR-015 часть 1, раздел 3): содержимое неизменяемо, статус меняет только
    определитель перехода. Текущая версия - последняя утверждённая
    (`document.current_version_id`)."""

    __tablename__ = "document_version"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    s3_key: Mapped[str]
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    # У kut_app колонка не читается (миграция c9e2d4b7a1f6): значение отдаёт app.version_comment.
    comment: Mapped[str | None] = mapped_column(String(2000), default=None)
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_version.id"), default=None, index=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_version_project_id_project",
        ),
        UniqueConstraint("s3_key", name="uq_document_version_s3_key"),
        Index(
            "uq_document_version_document_id_version_no", "document_id", "version_no", unique=True
        ),
        CheckConstraint("version_no >= 1", name="version_positive"),
        CheckConstraint(f"content_type IN ({_CONTENT_TYPES})", name="content_type_valid"),
        CheckConstraint(f"size_bytes > 0 AND size_bytes <= {MAX_FILE_BYTES}", name="size_valid"),
        CheckConstraint("checksum_sha256 ~ '^[0-9a-f]{64}$'", name="checksum_valid"),
        CheckConstraint(
            "position('/document/' || document_id::text || '/' IN s3_key) > 0",
            name="key_in_document",
        ),
        CheckConstraint(f"status IN ({_STATUSES})", name="status_valid"),
        CheckConstraint("comment IS NULL OR btrim(comment) <> ''", name="comment_not_blank"),
    )


class DocumentStatusEvent(BusinessBase):
    """Журнал переходов статуса версии (APPEND_ONLY): без UPDATE и DELETE."""

    __tablename__ = "document_status_event"

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_version.id"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(16), default=None)
    to_status: Mapped[str] = mapped_column(String(16))
    # У kut_app колонка не читается (миграция c9e2d4b7a1f6): значение отдаёт app.event_comment.
    comment: Mapped[str | None] = mapped_column(String(2000), default=None)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_status_event_project_id_project",
        ),
        CheckConstraint(f"to_status IN ({_STATUSES})", name="to_status_valid"),
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_STATUSES})", name="from_status_valid"
        ),
    )


class DocumentLink(BusinessBase):
    """Связь документа с сущностью (ADR часть 1 раздел 4): ровно одна цель.

    Связь не даёт доступа.
    """

    __tablename__ = "document_link"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    project_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_node.id"), default=None
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work.id"), default=None)
    work_actual_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_actual.id"), default=None
    )
    equipment_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("equipment_unit.id"), default=None
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_link_project_id_project",
        ),
        CheckConstraint(
            "num_nonnulls(project_node_id, work_id, work_actual_id, equipment_unit_id) = 1",
            name="one_target",
        ),
    )


class DocumentAmount(BusinessBase):
    """Сумма финансового документа (ADR часть 2 раздел 7.4, как project_finance): отдельная таблица,
    RLS по праву documents:read:financial; внешние - по своим документам."""

    __tablename__ = "document_amount"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), unique=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_amount_project_id_project",
        ),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
    )


class DocumentVersionAmount(BusinessBase):
    """Сумма версии (PO 2026-10-17, R1): одна строка на версию. Правка - только у черновика (гард
    БД),
    удаление запрещено. Видна по праву на вид и видимости версии; RLS не даёт сумму прорабу."""

    __tablename__ = "document_version_amount"

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"), unique=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_version_amount_project_id_project",
        ),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
    )


class DocumentNumberCounter(Base):
    """Счётчик регистрационных номеров (ADR часть 1, раздел 3.4): без дыр, меняет только функция.

    Политика «никому» закрывает таблицу для приложения; функция app.next_document_number работает
    от владельца kut_auth.
    """

    __tablename__ = "document_number_counter"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("legal_entity.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_no: Mapped[int] = mapped_column(Integer, server_default="0")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_valid"),
        CheckConstraint("last_no >= 0", name="last_no_not_negative"),
    )


class DocumentLine(BusinessBase):
    """Позиция версии (ADR-015 часть 4, S6a-1): наименование, ед., количество.
    Цена - в DocumentLinePrice. Вносится и меняется только в черновике.
    Удаление - мягкое (deleted_at), тоже в черновике."""

    __tablename__ = "document_line"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    position_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work.id"), default=None)
    project_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_node.id"), default=None
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_line_project_id_project",
        ),
        Index(
            "uq_document_line_version_id_position_no",
            "version_id",
            "position_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("position_no >= 1", name="position_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(unit) <> ''", name="unit_not_blank"),
    )


class DocumentLinePrice(BusinessBase):
    """Цена позиции (ADR-015 часть 4): под правом на деньги вида, как сумма версии. Сумма позиции -
    в представлении document_line_amount (RLS цен действует через него)."""

    __tablename__ = "document_line_price"

    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_line.id"), unique=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_line_price_project_id_project",
        ),
        CheckConstraint("unit_price >= 0", name="unit_price_not_negative"),
    )


ROUTE_ROLE_CHECK = (
    "role_code IN ('foreman', 'estimator', 'technical_director', 'finance_director', 'owner')"
)


class ApprovalRouteTemplate(BusinessBase):
    """Шаблон маршрута по группе видов (S6a-2, ADR-015 часть 5): `money` или `other`."""

    __tablename__ = "approval_route_template"

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))

    __table_args__ = (CheckConstraint("code IN ('money', 'other')", name="code"),)


class ApprovalRouteStepTemplate(BusinessBase):
    """Шаг шаблона маршрута: порядок и роль. Меняется функцией сохранения шаблона."""

    __tablename__ = "approval_route_step_template"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_route_template.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(String(40))

    __table_args__ = (
        Index(
            "uq_approval_route_step_template_template_id_position",
            "template_id",
            "position",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint(
            ROUTE_ROLE_CHECK,
            name="role_code",
        ),
    )


class VersionRouteStep(BusinessBase):
    """Шаг снимка маршрута на попытку (S6a-2). Снимок создаёт `route_send`;
    меняется только статус решения."""

    __tablename__ = "version_route_step"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"), index=True)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    attempt: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), default=None)
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_version_route_step_project_id_project",
        ),
        UniqueConstraint(
            "version_id",
            "attempt",
            "position",
            name="uq_version_route_step_version_attempt_pos",
        ),
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'skipped')", name="status"),
        CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL)"
            " OR (status IN ('approved', 'rejected') AND decided_at IS NOT NULL)"
            " OR (status = 'skipped' AND decided_at IS NULL)",
            name="decided_consistent",
        ),
    )


class ApprovalDecision(BusinessBase):
    """Решение по шагу (S6a-2), APPEND_ONLY: UPDATE и DELETE запрещены гардом. Причину приложение
    читает только функцией `app.decision_reason` (колоночный грант без `reason`)."""

    __tablename__ = "approval_decision"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"), index=True)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_entity.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    attempt: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), default=None)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_approval_decision_project_id_project",
        ),
        UniqueConstraint(
            "version_id",
            "attempt",
            "position",
            name="uq_approval_decision_version_attempt_pos",
        ),
        CheckConstraint("decision IN ('approve', 'reject')", name="decision"),
        CheckConstraint("decision = 'approve' OR btrim(reason) <> ''", name="reason_required"),
    )


class DocumentDelegation(BusinessBase):
    """Замещение на шаге (S6a-2, Q9): замещающий решает за замещаемого по роли на период."""

    __tablename__ = "document_delegation"

    delegator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    delegate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    role_code: Mapped[str] = mapped_column(String(40))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("valid_to >= valid_from", name="period"),
        CheckConstraint("valid_to - valid_from <= 60", name="max_period"),
        CheckConstraint("delegator_id <> delegate_id", name="not_self"),
        CheckConstraint(
            ROUTE_ROLE_CHECK,
            name="role_code",
        ),
    )
