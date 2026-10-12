"""equipment: схема SE-1 (ADR-014 часть 1 §1, часть 2 §5) - таблицы, ограничения, RLS, гранты, аудит

Revision ID: b3e9d7a1c5f2
Revises: c8f1e2a7b934
Create Date: 2026-10-12 10:00:00.000000

Таблицы модуля «Техника» SE-1: тип, единица реестра, норматив своей техники, шапка договора
`contract` (вид `rental_equipment`, решение K = A), тарифы аренды, машинисты, заявки, назначения
(решение M = C: часы в день на единицу и объект), ТО и ремонты, документы, журнал импорта. Путевые
листы, журнал затрат и сверка счетов - SE-2 и SE-3. Виды договоров `supply` и `subcontract` добавят
свои ревизии (S8, S11) вместе с правилами видимости.

Видимость (часть 2 §5): объектные строки - по `project_ids`, строки юрлица - по `legal_entity_ids`;
нормативы и тарифы - по `equipment:read:rates`; шапка аренды - по тому же гейту или по
`equipment:write` (ведёт реестр). Журнал аудита по деньгам закрыт RESTRICTIVE-политиками. Машинист
видит только свои назначения и единицы; ТО и документы ему не видны.

Правила, которые держит БД (лимит часов, ремонт, документы, состояние договора), - в ревизии
c5f1a8d2e904.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "b3e9d7a1c5f2"
down_revision: str | Sequence[str] | None = "c8f1e2a7b934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE = "SELECT, INSERT, UPDATE"
APPEND_ONLY = "SELECT, INSERT"
UNITS = ("hour", "motor_hour", "shift", "trip", "tonne", "month")
DOC_KINDS = ("passport", "technical_inspection", "insurance", "permit", "other")
CONTRACT_STATUSES = ("draft", "active", "closed", "terminated")
REQUEST_STATUSES = ("draft", "submitted", "assigned", "rejected", "cancelled", "closed")
ASSIGNMENT_STATUSES = ("planned", "finished", "cancelled")
MAINTENANCE_KINDS = ("to", "repair")
MAINTENANCE_STATUSES = ("planned", "in_progress", "done", "cancelled")
EMPLOYMENTS = ("staff", "lessor")
OWNERSHIPS = ("own", "rented")
IMPORT_STATUSES = ("checked", "committed", "rejected")

INTERNAL = "(SELECT app.is_internal())"
LEGAL = "((SELECT app.legal_entity_ids())::uuid[])"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
IS_MACHINIST = "(SELECT app.is_machinist())"
CAN_READ = "(SELECT app.has_permission('equipment', 'read'))"
CAN_WRITE = "(SELECT app.has_permission('equipment', 'write'))"
CAN_READ_RATES = "(SELECT app.has_permission('equipment', 'read', 'rates'))"
CAN_APPROVE_RATE = "(SELECT app.has_permission('equipment', 'approve', 'rate'))"
CAN_WRITE_REQUEST = "(SELECT app.has_permission('equipment', 'write', 'request'))"
CAN_APPROVE_ASSIGN = "(SELECT app.has_permission('equipment', 'approve', 'assign'))"
CAN_WRITE_MAINTENANCE = "(SELECT app.has_permission('equipment', 'write', 'maintenance'))"
CAN_FINANCE_WRITE = "(SELECT app.has_permission('finance', 'write'))"

# Таблицы модуля: на них включается RLS; определители (SECURITY DEFINER от kut_auth) читают их.
MODULE_TABLES = (
    "equipment_type",
    "contract",
    "equipment_unit",
    "equipment_own_rate",
    "equipment_rate",
    "equipment_operator",
    "equipment_request",
    "equipment_assignment",
    "equipment_maintenance",
    "equipment_document",
    "equipment_import_batch",
)

IS_MACHINIST_FUNCTION = """
CREATE FUNCTION app.is_machinist() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public."user" usr
        JOIN public.role r ON r.id = usr.role_id
        WHERE usr.id = app.user_id() AND r.code = 'machinist')
$$
"""

UNIT_VISIBLE_FUNCTION = """
CREATE FUNCTION app.equipment_unit_visible(p_unit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT NOT app.is_machinist() OR EXISTS (
        SELECT 1 FROM public.equipment_assignment a
        JOIN public.equipment_operator o ON o.id = a.operator_id AND o.deleted_at IS NULL
        WHERE a.equipment_unit_id = p_unit AND a.deleted_at IS NULL
          AND o.user_id = app.user_id())
$$
"""


def _check(table: str, suffix: str, expression: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=op.f(f"ck_{table}_{suffix}"))


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _policy(table: str, command: str, using: str, check: str | None = None) -> None:
    if command == "SELECT":
        op.execute(
            f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app USING ({using})"
        )
    elif command == "INSERT":
        op.execute(
            f"CREATE POLICY {table}_insert_app ON {table} FOR INSERT TO kut_app WITH CHECK ({using})"
        )
    else:
        op.execute(
            f"CREATE POLICY {table}_update_app ON {table} FOR UPDATE TO kut_app"
            f" USING ({using}) WITH CHECK ({check or using})"
        )


def _create_equipment_type() -> None:
    business_table(
        "equipment_type",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("default_unit", sa.String(), nullable=False),
        _check(
            "equipment_type", "code_and_name_not_blank", "btrim(code) <> '' AND btrim(name) <> ''"
        ),
        _check("equipment_type", "default_unit_valid", f"default_unit IN ({_in(UNITS)})"),
    )
    op.create_index(
        "uq_equipment_type_code",
        "equipment_type",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_contract() -> None:
    business_table(
        "contract",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("counterparty_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_contract_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_contract_counterparty_id_counterparty"),
        ),
        _check("contract", "kind_valid", "kind IN ('rental_equipment')"),
        _check("contract", "status_valid", f"status IN ({_in(CONTRACT_STATUSES)})"),
        _check("contract", "number_not_blank", "btrim(number) <> ''"),
        _check("contract", "dates_ordered", "date_to IS NULL OR date_to >= date_from"),
    )
    op.create_index(
        "uq_contract_legal_entity_id_kind_number",
        "contract",
        ["legal_entity_id", "kind", "number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_contract_counterparty_id", "contract", ["counterparty_id"])


def _create_equipment_unit() -> None:
    business_table(
        "equipment_unit",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_type_id", sa.Uuid(), nullable=False),
        sa.Column("ownership", sa.String(), nullable=False),
        sa.Column("registry_key", sa.String(), nullable=False),
        sa.Column("registry_number", sa.String(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_unit_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_type_id"],
            ["equipment_type.id"],
            name=op.f("fk_equipment_unit_equipment_type_id_equipment_type"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["contract.id"], name=op.f("fk_equipment_unit_contract_id_contract")
        ),
        _check("equipment_unit", "ownership_valid", f"ownership IN ({_in(OWNERSHIPS)})"),
        _check(
            "equipment_unit",
            "registry_not_blank",
            "btrim(registry_key) <> '' AND btrim(registry_number) <> ''",
        ),
        # Своя единица договора не имеет, арендованная обязана иметь договор аренды.
        _check(
            "equipment_unit",
            "contract_matches_ownership",
            "(ownership = 'own' AND contract_id IS NULL)"
            " OR (ownership = 'rented' AND contract_id IS NOT NULL)",
        ),
    )
    op.create_index(
        "uq_equipment_unit_legal_entity_id_registry_key",
        "equipment_unit",
        ["legal_entity_id", "registry_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_equipment_unit_equipment_type_id", "equipment_unit", ["equipment_type_id"])
    op.create_index("ix_equipment_unit_contract_id", "equipment_unit", ["contract_id"])


def _create_own_rate() -> None:
    business_table(
        "equipment_own_rate",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_type_id", sa.Uuid(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("proposed_by", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_own_rate_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_type_id"],
            ["equipment_type.id"],
            name=op.f("fk_equipment_own_rate_equipment_type_id_equipment_type"),
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by"], ["user.id"], name=op.f("fk_equipment_own_rate_proposed_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["user.id"], name=op.f("fk_equipment_own_rate_approved_by_user")
        ),
        _check("equipment_own_rate", "unit_valid", f"unit IN ({_in(UNITS)})"),
        _check("equipment_own_rate", "amount_not_negative", "amount >= 0"),
        # Предложивший не утверждает свой норматив (часть 2, D7).
        _check(
            "equipment_own_rate",
            "approver_is_not_proposer",
            "approved_by IS NULL OR approved_by <> proposed_by",
        ),
    )
    op.create_index(
        "uq_equipment_own_rate_version",
        "equipment_own_rate",
        ["legal_entity_id", "equipment_type_id", "unit", "valid_from"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_rate() -> None:
    business_table(
        "equipment_rate",
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("with_operator", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("min_quantity", sa.Numeric(10, 2), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["contract.id"], name=op.f("fk_equipment_rate_contract_id_contract")
        ),
        _check("equipment_rate", "unit_valid", f"unit IN ({_in(UNITS)})"),
        _check("equipment_rate", "amount_not_negative", "amount >= 0"),
        _check(
            "equipment_rate",
            "min_quantity_not_negative",
            "min_quantity IS NULL OR min_quantity >= 0",
        ),
        # Минимум задаётся количеством за календарный месяц; фикс за месяц минимума не имеет.
        _check("equipment_rate", "month_has_no_minimum", "unit <> 'month' OR min_quantity IS NULL"),
    )
    op.create_index(
        "uq_equipment_rate_version",
        "equipment_rate",
        ["contract_id", "unit", "with_operator", "valid_from"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def _create_operator() -> None:
    business_table(
        "equipment_operator",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("employment", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_operator_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_equipment_operator_user_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_equipment_operator_counterparty_id_counterparty"),
        ),
        _check("equipment_operator", "employment_valid", f"employment IN ({_in(EMPLOYMENTS)})"),
        _check("equipment_operator", "full_name_not_blank", "btrim(full_name) <> ''"),
        # Штатный машинист - учётная запись сотрудника; машинист арендодателя учётной записи не имеет.
        _check(
            "equipment_operator",
            "employment_matches_account",
            "(employment = 'staff' AND user_id IS NOT NULL AND counterparty_id IS NULL)"
            " OR (employment = 'lessor' AND user_id IS NULL AND counterparty_id IS NOT NULL)",
        ),
    )
    op.create_index(
        "uq_equipment_operator_user_id",
        "equipment_operator",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND user_id IS NOT NULL"),
    )


def _create_request() -> None:
    business_table(
        "equipment_request",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_item_id", sa.Uuid(), nullable=True),
        sa.Column("equipment_type_id", sa.Uuid(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("planned_hours_per_day", sa.Numeric(4, 2), nullable=False),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("reject_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name=op.f("fk_equipment_request_project_id_project"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name=op.f("fk_equipment_request_project_id_project_node"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schedule_item_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name=op.f("fk_equipment_request_project_id_schedule_item"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_type_id"],
            ["equipment_type.id"],
            name=op.f("fk_equipment_request_equipment_type_id_equipment_type"),
        ),
        sa.UniqueConstraint("project_id", "id", name=op.f("uq_equipment_request_project_id_id")),
        _check("equipment_request", "status_valid", f"status IN ({_in(REQUEST_STATUSES)})"),
        _check(
            "equipment_request",
            "hours_in_day",
            "planned_hours_per_day > 0 AND planned_hours_per_day <= 24",
        ),
        _check("equipment_request", "dates_ordered", "date_to >= date_from"),
        _check(
            "equipment_request",
            "dates_in_range",
            "date_from >= DATE '2000-01-01' AND date_to <= DATE '2100-12-31'",
        ),
        _check(
            "equipment_request",
            "reject_reason_required",
            "status <> 'rejected' OR btrim(reject_reason) <> ''",
        ),
    )
    op.create_index("ix_equipment_request_project_id", "equipment_request", ["project_id"])
    op.create_index(
        "ix_equipment_request_equipment_type_id", "equipment_request", ["equipment_type_id"]
    )


def _create_assignment() -> None:
    business_table(
        "equipment_assignment",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_item_id", sa.Uuid(), nullable=True),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("planned_hours_per_day", sa.Numeric(4, 2), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(), server_default="planned", nullable=False),
        sa.Column("cancel_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name=op.f("fk_equipment_assignment_project_id_project"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name=op.f("fk_equipment_assignment_project_id_project_node"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schedule_item_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name=op.f("fk_equipment_assignment_project_id_schedule_item"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "request_id"],
            ["equipment_request.project_id", "equipment_request.id"],
            name=op.f("fk_equipment_assignment_project_id_equipment_request"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_equipment_assignment_equipment_unit_id_equipment_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["equipment_operator.id"],
            name=op.f("fk_equipment_assignment_operator_id_equipment_operator"),
        ),
        _check("equipment_assignment", "status_valid", f"status IN ({_in(ASSIGNMENT_STATUSES)})"),
        _check(
            "equipment_assignment",
            "hours_in_day",
            "planned_hours_per_day > 0 AND planned_hours_per_day <= 24",
        ),
        _check("equipment_assignment", "dates_ordered", "date_to >= date_from"),
        _check(
            "equipment_assignment",
            "dates_in_range",
            "date_from >= DATE '2000-01-01' AND date_to <= DATE '2100-12-31'",
        ),
        _check(
            "equipment_assignment",
            "cancel_reason_required",
            "status <> 'cancelled' OR btrim(cancel_reason) <> ''",
        ),
    )
    op.create_index(
        "ix_equipment_assignment_equipment_unit_id_date_from",
        "equipment_assignment",
        ["equipment_unit_id", "date_from"],
    )
    op.create_index("ix_equipment_assignment_project_id", "equipment_assignment", ["project_id"])
    op.create_index("ix_equipment_assignment_operator_id", "equipment_assignment", ["operator_id"])


def _create_maintenance() -> None:
    business_table(
        "equipment_maintenance",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("planned_from", sa.Date(), nullable=False),
        sa.Column("planned_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), server_default="planned", nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_maintenance_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_equipment_maintenance_equipment_unit_id_equipment_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_equipment_maintenance_project_id_project")
        ),
        _check("equipment_maintenance", "kind_valid", f"kind IN ({_in(MAINTENANCE_KINDS)})"),
        _check("equipment_maintenance", "status_valid", f"status IN ({_in(MAINTENANCE_STATUSES)})"),
        _check("equipment_maintenance", "title_not_blank", "btrim(title) <> ''"),
        _check(
            "equipment_maintenance",
            "dates_ordered",
            "planned_to IS NULL OR planned_to >= planned_from",
        ),
        _check("equipment_maintenance", "done_has_time", "status <> 'done' OR done_at IS NOT NULL"),
    )
    op.create_index(
        "ix_equipment_maintenance_equipment_unit_id", "equipment_maintenance", ["equipment_unit_id"]
    )


def _create_document() -> None:
    business_table(
        "equipment_document",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_document_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_equipment_document_equipment_unit_id_equipment_unit"),
        ),
        sa.UniqueConstraint(
            "equipment_unit_id",
            "kind",
            "version",
            name=op.f("uq_equipment_document_equipment_unit_id_kind_version"),
        ),
        _check("equipment_document", "kind_valid", f"kind IN ({_in(DOC_KINDS)})"),
        _check("equipment_document", "number_not_blank", "btrim(number) <> ''"),
        _check("equipment_document", "version_positive", "version >= 1"),
    )


def _create_import_batch() -> None:
    business_table(
        "equipment_import_batch",
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column(
            "errors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        _check("equipment_import_batch", "kind_valid", "kind = 'units'"),
        _check("equipment_import_batch", "status_valid", f"status IN ({_in(IMPORT_STATUSES)})"),
        _check("equipment_import_batch", "sha256_length", "char_length(file_sha256) = 64"),
        _check(
            "equipment_import_batch",
            "counts_not_negative",
            "created_count >= 0 AND updated_count >= 0 AND unchanged_count >= 0"
            " AND error_count >= 0",
        ),
    )


def _create_definers() -> None:
    # Функции создаёт и права на них меняет владелец kut_auth (как has_permission): REVOKE от
    # kut_owner без роли владельца отказывает.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(IS_MACHINIST_FUNCTION)
    op.execute(UNIT_VISIBLE_FUNCTION)
    for function in ("app.is_machinist()", "app.equipment_unit_visible(uuid)"):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO kut_app, kut_worker")
    op.execute("RESET ROLE")


def _enable_rls_and_policies() -> None:
    for table in MODULE_TABLES:
        force_rls(table)

    _policy("equipment_type", "SELECT", f"{INTERNAL} AND {CAN_READ}")
    _policy("equipment_type", "INSERT", f"{INTERNAL} AND {CAN_WRITE}")
    _policy("equipment_type", "UPDATE", f"{INTERNAL} AND {CAN_WRITE}")

    # Шапка аренды: видят тарифный круг и ведущие реестр (equipment:write). Прораб и мастер - нет.
    _policy(
        "contract",
        "SELECT",
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL} AND ({CAN_READ_RATES} OR {CAN_WRITE})",
    )
    _policy(
        "contract",
        "INSERT",
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL} AND {CAN_FINANCE_WRITE}",
    )
    _policy(
        "contract",
        "UPDATE",
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL} AND {CAN_FINANCE_WRITE}",
    )

    unit_scope = f"{INTERNAL} AND legal_entity_id = ANY {LEGAL}"
    _policy(
        "equipment_unit",
        "SELECT",
        f"{unit_scope} AND {CAN_READ} AND app.equipment_unit_visible(id)",
    )
    _policy("equipment_unit", "INSERT", f"{unit_scope} AND {CAN_WRITE}")
    _policy("equipment_unit", "UPDATE", f"{unit_scope} AND {CAN_WRITE}")

    # Нормативы: читают с гейтом денег; предлагает - писатель реестра (с гейтом), утверждает - право
    # approve:rate. Изменить строку может только утверждающий (сама утверждение - UPDATE approved_by).
    rate_scope = f"{INTERNAL} AND legal_entity_id = ANY {LEGAL} AND {CAN_READ_RATES}"
    _policy("equipment_own_rate", "SELECT", rate_scope)
    _policy("equipment_own_rate", "INSERT", f"{rate_scope} AND {CAN_WRITE}")
    _policy("equipment_own_rate", "UPDATE", f"{rate_scope} AND {CAN_APPROVE_RATE}")

    # Тарифы - строки договора: видят те же, что деньги; пишет финансовый контур.
    rate_contract = "EXISTS (SELECT 1 FROM contract c WHERE c.id = contract_id)"
    _policy("equipment_rate", "SELECT", f"{INTERNAL} AND {CAN_READ_RATES} AND {rate_contract}")
    _policy(
        "equipment_rate",
        "INSERT",
        f"{INTERNAL} AND {CAN_FINANCE_WRITE} AND {CAN_READ_RATES} AND {rate_contract}",
    )
    _policy(
        "equipment_rate",
        "UPDATE",
        f"{INTERNAL} AND {CAN_FINANCE_WRITE} AND {CAN_READ_RATES} AND {rate_contract}",
    )

    # Машинист видит в справочнике только себя (учётная запись); машинист арендодателя - по праву.
    operator_scope = f"{INTERNAL} AND legal_entity_id = ANY {LEGAL}"
    _policy(
        "equipment_operator",
        "SELECT",
        f"{operator_scope} AND {CAN_READ} AND (NOT {IS_MACHINIST} OR user_id = app.user_id())",
    )
    _policy("equipment_operator", "INSERT", f"{operator_scope} AND {CAN_WRITE}")
    _policy("equipment_operator", "UPDATE", f"{operator_scope} AND {CAN_WRITE}")

    # Заявки: прораб своего объекта создаёт и отзывает свои; начальник и техдиректор решают.
    # Машинист заявок не видит (в матрице его строки нет).
    request_scope = f"{INTERNAL} AND project_id = ANY {PROJECTS}"
    _policy("equipment_request", "SELECT", f"{request_scope} AND {CAN_READ} AND NOT {IS_MACHINIST}")
    _policy("equipment_request", "INSERT", f"{request_scope} AND {CAN_WRITE_REQUEST}")
    _policy(
        "equipment_request",
        "UPDATE",
        f"{request_scope} AND (({CAN_WRITE_REQUEST} AND created_by = app.user_id())"
        f" OR {CAN_APPROVE_ASSIGN})",
    )

    # Назначения: назначает начальник отдела (резерв - техдиректор). Машинист видит свои.
    _policy(
        "equipment_assignment",
        "SELECT",
        f"{request_scope} AND {CAN_READ} AND (NOT {IS_MACHINIST} OR operator_id IN"
        " (SELECT o.id FROM equipment_operator o WHERE o.user_id = app.user_id()))",
    )
    _policy("equipment_assignment", "INSERT", f"{request_scope} AND {CAN_APPROVE_ASSIGN}")
    _policy("equipment_assignment", "UPDATE", f"{request_scope} AND {CAN_APPROVE_ASSIGN}")

    # ТО, ремонты и документы: чтение по equipment:read (часть 2 §5), запись - write:maintenance.
    # Машинист их не видит (матрица: «—»).
    maintenance_scope = f"{INTERNAL} AND legal_entity_id = ANY {LEGAL}"
    maintenance_read = f"{maintenance_scope} AND {CAN_READ} AND NOT {IS_MACHINIST}"
    _policy("equipment_maintenance", "SELECT", maintenance_read)
    _policy("equipment_maintenance", "INSERT", f"{maintenance_scope} AND {CAN_WRITE_MAINTENANCE}")
    _policy("equipment_maintenance", "UPDATE", f"{maintenance_scope} AND {CAN_WRITE_MAINTENANCE}")
    _policy("equipment_document", "SELECT", maintenance_read)
    _policy("equipment_document", "INSERT", f"{maintenance_scope} AND {CAN_WRITE_MAINTENANCE}")

    # Журнал импорта: тот же круг, что пишет реестр.
    _policy("equipment_import_batch", "SELECT", f"{INTERNAL} AND {CAN_WRITE}")
    _policy("equipment_import_batch", "INSERT", f"{INTERNAL} AND {CAN_WRITE}")


def _grants() -> None:
    for table in (
        "equipment_type",
        "contract",
        "equipment_unit",
        "equipment_own_rate",
        "equipment_rate",
        "equipment_operator",
        "equipment_request",
        "equipment_assignment",
        "equipment_maintenance",
    ):
        grant(table, app=READ_WRITE, worker=READ_WRITE)
    grant("equipment_document", app=APPEND_ONLY, worker=APPEND_ONLY)
    grant("equipment_import_batch", app=APPEND_ONLY, worker=APPEND_ONLY)
    # Определители читают строки всех объектов (BYPASSRLS); блокировка единицы - SELECT FOR UPDATE,
    # а он требует UPDATE хотя бы одной колонки: выдаём UPDATE(id), который триггер не даёт менять.
    op.execute(f"GRANT SELECT ON {', '.join(MODULE_TABLES)} TO kut_auth")
    op.execute("GRANT UPDATE (id) ON equipment_unit TO kut_auth")


def _audit() -> None:
    create_audit_triggers("equipment_type")
    create_audit_triggers(
        "contract",
        legal_entity_column="legal_entity_id",
        frozen_columns=("legal_entity_id", "kind"),
    )
    create_audit_triggers(
        "equipment_unit", legal_entity_column="legal_entity_id", frozen_columns=("legal_entity_id",)
    )
    create_audit_triggers(
        "equipment_own_rate",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "legal_entity_id",
            "equipment_type_id",
            "unit",
            "amount",
            "valid_from",
            "proposed_by",
        ),
    )
    create_audit_triggers(
        "equipment_rate",
        frozen_columns=(
            "contract_id",
            "unit",
            "with_operator",
            "amount",
            "min_quantity",
            "valid_from",
        ),
    )
    create_audit_triggers(
        "equipment_operator",
        legal_entity_column="legal_entity_id",
        frozen_columns=("legal_entity_id", "employment"),
    )
    create_audit_triggers(
        "equipment_request",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id"),
    )
    create_audit_triggers(
        "equipment_assignment",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id", "equipment_unit_id", "request_id"),
    )
    create_audit_triggers(
        "equipment_maintenance",
        legal_entity_column="legal_entity_id",
        frozen_columns=("legal_entity_id", "equipment_unit_id", "kind"),
    )
    create_audit_triggers(
        "equipment_document",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "legal_entity_id",
            "equipment_unit_id",
            "kind",
            "number",
            "valid_until",
            "version",
        ),
    )
    create_audit_triggers("equipment_import_batch")
    # Журнал аудита хранит старые и новые значения: нормативы, тарифы и шапку аренды читают те же,
    # кто видит их в таблице (RESTRICTIVE поверх прежних политик журнала, как у project_finance).
    op.execute(
        "CREATE POLICY audit_log_equipment_rates ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        " USING ((table_name IS DISTINCT FROM 'equipment_own_rate'"
        " AND table_name IS DISTINCT FROM 'equipment_rate') OR " + CAN_READ_RATES + ")"
    )
    op.execute(
        "CREATE POLICY audit_log_equipment_contracts ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        " USING (table_name IS DISTINCT FROM 'contract' OR "
        + CAN_READ_RATES
        + " OR "
        + CAN_WRITE
        + ")"
    )


def upgrade() -> None:
    _create_equipment_type()
    _create_contract()
    _create_equipment_unit()
    _create_own_rate()
    _create_rate()
    _create_operator()
    _create_request()
    _create_assignment()
    _create_maintenance()
    _create_document()
    _create_import_batch()
    _create_definers()
    _enable_rls_and_policies()
    _grants()
    _audit()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_log_equipment_contracts ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_log_equipment_rates ON audit_log")
    for table in (
        "equipment_import_batch",
        "equipment_document",
        "equipment_maintenance",
        "equipment_assignment",
        "equipment_request",
        "equipment_operator",
        "equipment_rate",
        "equipment_own_rate",
        "equipment_unit",
        "contract",
        "equipment_type",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS app.equipment_unit_visible(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.is_machinist()")
