"""equipment: путевые листы, ГСМ, делегаты и журнал затрат - схема SE-2a (ADR-014 часть 1 §1, часть 2 §3-5)

Revision ID: e4f1a9c3b7d2
Revises: d7b2e9c1a4f8
Create Date: 2026-10-13 10:00:00.000000

- equipment_shift: лист = единица × день × объект × машинист (решение M = C). Одна живая запись на такой
  ключ (уникальный индекс); суточный лимит 24 ч по всем объектам - функция в ревизии b6d2f8a4c1e9.
- equipment_shift_line: разнесение листа по узлам и работам; сумма строк равна итогу (отложенный триггер).
- equipment_fuel: заправка строкой; сумма не отрицательна; сторно - функцией, строка не меняется.
- equipment_delegate: делегат подтверждения на период (решение Q5 A).
- cost_entry: журнал затрат. Пишут только определители (kut_auth), читает гейт read:rates; UPDATE и
  DELETE запрещены правами и триггером (ревизия b6d2f8a4c1e9).
- equipment_maintenance: колонки суммы нет (ADR, часть 1 §1): сумма ремонта уходит в cost_entry при
  закрытии через функцию (ревизия b6d2f8a4c1e9).

Видимость: объектные строки - по project_ids; журнал и суммы - гейт read:rates; машинист видит свои листы.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "e4f1a9c3b7d2"
down_revision: str | Sequence[str] | None = "d7b2e9c1a4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE = "SELECT, INSERT, UPDATE"
APPEND_ONLY = "SELECT, INSERT"
UNITS = ("hour", "motor_hour", "shift", "trip", "tonne", "month")
SHIFT_STATUSES = ("draft", "submitted", "confirmed", "rejected")
CATEGORIES = (
    "equipment_own",
    "equipment_rental",
    "equipment_fixed",
    "equipment_min_payment",
    "equipment_fuel",
    "equipment_repair",
)
SOURCE_TYPES = ("equipment_shift", "equipment_fuel", "equipment_maintenance")

INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL = "((SELECT app.legal_entity_ids())::uuid[])"
IS_MACHINIST = "(SELECT app.is_machinist())"
CAN_READ = "(SELECT app.has_permission('equipment', 'read'))"
CAN_READ_RATES = "(SELECT app.has_permission('equipment', 'read', 'rates'))"
CAN_WRITE_SHIFT = "(SELECT app.has_permission('equipment', 'write', 'shift'))"
CAN_APPROVE_SHIFT = "(SELECT app.has_permission('equipment', 'approve', 'shift'))"
CAN_AFTER_APPROVAL = "(SELECT app.has_permission('equipment', 'write', 'after_approval'))"
OWN_OPERATOR_OF_SHIFT = (
    "EXISTS (SELECT 1 FROM public.equipment_operator o"
    " WHERE o.id = equipment_shift.operator_id AND o.user_id = app.user_id())"
)
DATE_RANGE = "{column} >= DATE '2000-01-01' AND {column} <= DATE '2100-12-31'"

# Делегат подтверждения: право подтверждения на объект в дату листа (решение Q5 A). Определитель
# без параметра пользователя: берёт app.user_id(), поэтому не служит оракулом чужих делегатов.
DELEGATED_FUNCTION = """
CREATE FUNCTION app.equipment_delegated(p_project uuid, p_day date) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.equipment_delegate d
        WHERE d.project_id = p_project AND d.delegate_user_id = app.user_id()
          AND d.deleted_at IS NULL AND d.revoked_at IS NULL
          AND p_day BETWEEN d.date_from AND d.date_to)
$$
"""


def _policy(table: str, command: str, using: str, check: str | None = None) -> None:
    if command == "SELECT":
        op.execute(
            f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app USING ({using})"
        )
    elif command == "INSERT":
        op.execute(
            f"CREATE POLICY {table}_insert_app ON {table} FOR INSERT TO kut_app WITH CHECK ({check or using})"
        )
    else:
        op.execute(
            f"CREATE POLICY {table}_update_app ON {table} FOR UPDATE TO kut_app"
            f" USING ({using}) WITH CHECK ({check or using})"
        )


def _create_shift() -> None:
    business_table(
        "equipment_shift",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("hours", sa.Numeric(4, 2), nullable=False),
        sa.Column("odometer_from", sa.Numeric(10, 2), nullable=True),
        sa.Column("odometer_to", sa.Numeric(10, 2), nullable=True),
        sa.Column("trips", sa.Integer(), nullable=True),
        sa.Column("tonnes", sa.Numeric(10, 3), nullable=True),
        sa.Column("downtime_hours", sa.Numeric(4, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("downtime_reason", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("reject_reason", sa.String(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posting_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lines_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_shift_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name=op.f("fk_equipment_shift_project_id_project"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_equipment_shift_equipment_unit_id_equipment_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["equipment_assignment.id"],
            name=op.f("fk_equipment_shift_assignment_id_equipment_assignment"),
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"],
            ["equipment_operator.id"],
            name=op.f("fk_equipment_shift_operator_id_equipment_operator"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["user.id"], name=op.f("fk_equipment_shift_confirmed_by_user")
        ),
        sa.UniqueConstraint("project_id", "id", name=op.f("uq_equipment_shift_project_id_id")),
        sa.CheckConstraint(
            f"status IN ({_in(SHIFT_STATUSES)})", name=op.f("ck_equipment_shift_status_valid")
        ),
        sa.CheckConstraint("source IN ('manual')", name=op.f("ck_equipment_shift_source_valid")),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24", name=op.f("ck_equipment_shift_hours_in_day")
        ),
        sa.CheckConstraint(
            "downtime_hours >= 0 AND downtime_hours <= hours",
            name=op.f("ck_equipment_shift_downtime_valid"),
        ),
        sa.CheckConstraint(
            "downtime_hours = 0 OR btrim(coalesce(downtime_reason, '')) <> ''",
            name=op.f("ck_equipment_shift_downtime_reason_required"),
        ),
        sa.CheckConstraint(
            "(odometer_from IS NULL OR odometer_from >= 0)"
            " AND (odometer_to IS NULL OR (odometer_from IS NOT NULL AND odometer_to >= odometer_from))",
            name=op.f("ck_equipment_shift_odometer_valid"),
        ),
        sa.CheckConstraint(
            "trips IS NULL OR trips >= 0", name=op.f("ck_equipment_shift_trips_not_negative")
        ),
        sa.CheckConstraint(
            "tonnes IS NULL OR tonnes >= 0", name=op.f("ck_equipment_shift_tonnes_not_negative")
        ),
        sa.CheckConstraint(
            DATE_RANGE.format(column="work_date"), name=op.f("ck_equipment_shift_dates_in_range")
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR btrim(coalesce(reject_reason, '')) <> ''",
            name=op.f("ck_equipment_shift_reject_reason_required"),
        ),
        sa.CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)",
            name=op.f("ck_equipment_shift_confirmed_fields"),
        ),
        sa.CheckConstraint(
            "confirmed_by IS NULL OR created_by IS NULL OR confirmed_by <> created_by",
            name=op.f("ck_equipment_shift_confirmer_is_not_author"),
        ),
        sa.CheckConstraint(
            "posting_version >= 0 AND lines_revision >= 0",
            name=op.f("ck_equipment_shift_counters_not_negative"),
        ),
    )
    op.create_index(
        "uq_equipment_shift_key",
        "equipment_shift",
        ["equipment_unit_id", "work_date", "project_id", "operator_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_equipment_shift_project_id", "equipment_shift", ["project_id"])
    op.create_index(
        "ix_equipment_shift_equipment_unit_id_work_date",
        "equipment_shift",
        ["equipment_unit_id", "work_date"],
    )
    op.create_index("ix_equipment_shift_operator_id", "equipment_shift", ["operator_id"])
    op.create_index("ix_equipment_shift_assignment_id", "equipment_shift", ["assignment_id"])


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _create_shift_line() -> None:
    business_table(
        "equipment_shift_line",
        sa.Column("shift_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_item_id", sa.Uuid(), nullable=True),
        sa.Column("hours", sa.Numeric(4, 2), nullable=False),
        sa.Column("trips", sa.Integer(), nullable=True),
        sa.Column("tonnes", sa.Numeric(10, 3), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "shift_id"],
            ["equipment_shift.project_id", "equipment_shift.id"],
            name=op.f("fk_equipment_shift_line_project_id_equipment_shift"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name=op.f("fk_equipment_shift_line_project_id_project_node"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schedule_item_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name=op.f("fk_equipment_shift_line_project_id_schedule_item"),
        ),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24", name=op.f("ck_equipment_shift_line_hours_in_day")
        ),
        sa.CheckConstraint(
            "trips IS NULL OR trips >= 0", name=op.f("ck_equipment_shift_line_trips_not_negative")
        ),
        sa.CheckConstraint(
            "tonnes IS NULL OR tonnes >= 0",
            name=op.f("ck_equipment_shift_line_tonnes_not_negative"),
        ),
    )
    op.create_index("ix_equipment_shift_line_shift_id", "equipment_shift_line", ["shift_id"])


def _create_fuel() -> None:
    business_table(
        "equipment_fuel",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=False),
        sa.Column("fuel_date", sa.Date(), nullable=False),
        sa.Column("liters", sa.Numeric(10, 2), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.String(), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by", sa.Uuid(), nullable=True),
        sa.Column("reverse_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_equipment_fuel_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name=op.f("fk_equipment_fuel_project_id_project"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_equipment_fuel_equipment_unit_id_equipment_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["user.id"], name=op.f("fk_equipment_fuel_reversed_by_user")
        ),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_equipment_fuel_amount_not_negative")),
        sa.CheckConstraint(
            "liters IS NULL OR liters >= 0", name=op.f("ck_equipment_fuel_liters_not_negative")
        ),
        sa.CheckConstraint("source IN ('manual')", name=op.f("ck_equipment_fuel_source_valid")),
        sa.CheckConstraint(
            DATE_RANGE.format(column="fuel_date"), name=op.f("ck_equipment_fuel_dates_in_range")
        ),
        sa.CheckConstraint(
            "(reversed_at IS NULL) = (reversed_by IS NULL)"
            " AND (reversed_at IS NULL) = (reverse_reason IS NULL)",
            name=op.f("ck_equipment_fuel_reversal_complete"),
        ),
        sa.CheckConstraint(
            "reverse_reason IS NULL OR btrim(reverse_reason) <> ''",
            name=op.f("ck_equipment_fuel_reverse_reason_not_blank"),
        ),
    )
    op.create_index(
        "ix_equipment_fuel_equipment_unit_id_fuel_date",
        "equipment_fuel",
        ["equipment_unit_id", "fuel_date"],
    )
    op.create_index("ix_equipment_fuel_project_id", "equipment_fuel", ["project_id"])


def _create_delegate() -> None:
    business_table(
        "equipment_delegate",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_user_id", sa.Uuid(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_equipment_delegate_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["delegate_user_id"],
            ["user.id"],
            name=op.f("fk_equipment_delegate_delegate_user_id_user"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["user.id"], name=op.f("fk_equipment_delegate_revoked_by_user")
        ),
        sa.CheckConstraint(
            "date_to >= date_from", name=op.f("ck_equipment_delegate_dates_ordered")
        ),
        sa.CheckConstraint(
            DATE_RANGE.format(column="date_from") + " AND " + DATE_RANGE.format(column="date_to"),
            name=op.f("ck_equipment_delegate_dates_in_range"),
        ),
        sa.CheckConstraint(
            "btrim(reason) <> ''", name=op.f("ck_equipment_delegate_reason_not_blank")
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by IS NULL)",
            name=op.f("ck_equipment_delegate_revocation_complete"),
        ),
    )
    op.create_index("ix_equipment_delegate_project_id", "equipment_delegate", ["project_id"])
    op.create_index(
        "ix_equipment_delegate_delegate_user_id", "equipment_delegate", ["delegate_user_id"]
    )


def _create_cost_entry() -> None:
    business_table(
        "cost_entry",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("node_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_item_id", sa.Uuid(), nullable=True),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("posting_version", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("tariff_unit", sa.String(), nullable=True),
        sa.Column("rate", sa.Numeric(14, 2), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reversal_of_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_cost_entry_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name=op.f("fk_cost_entry_project_id_project"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name=op.f("fk_cost_entry_project_id_project_node"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schedule_item_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name=op.f("fk_cost_entry_project_id_schedule_item"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_cost_entry_equipment_unit_id_equipment_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id"],
            ["cost_entry.id"],
            name=op.f("fk_cost_entry_reversal_of_id_cost_entry"),
        ),
        sa.CheckConstraint(
            f"category IN ({_in(CATEGORIES)})", name=op.f("ck_cost_entry_category_valid")
        ),
        sa.CheckConstraint(
            f"source_type IN ({_in(SOURCE_TYPES)})", name=op.f("ck_cost_entry_source_type_valid")
        ),
        sa.CheckConstraint(
            f"tariff_unit IS NULL OR tariff_unit IN ({_in(UNITS)})",
            name=op.f("ck_cost_entry_tariff_unit_valid"),
        ),
        sa.CheckConstraint(
            "posting_version >= 1", name=op.f("ck_cost_entry_posting_version_positive")
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_cost_entry_line_no_positive")),
        sa.CheckConstraint(
            "(reversal_of_id IS NULL AND amount >= 0) OR (reversal_of_id IS NOT NULL AND amount <= 0)",
            name=op.f("ck_cost_entry_amount_sign"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR (reversal_of_id IS NULL AND quantity >= 0)"
            " OR (reversal_of_id IS NOT NULL AND quantity <= 0)",
            name=op.f("ck_cost_entry_quantity_sign"),
        ),
        sa.CheckConstraint(
            "rate IS NULL OR rate >= 0", name=op.f("ck_cost_entry_rate_not_negative")
        ),
    )
    op.create_index(
        "uq_cost_entry_source_line",
        "cost_entry",
        ["source_type", "source_id", "posting_version", "line_no"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NULL"),
    )
    op.create_index(
        "uq_cost_entry_reversal_of_id",
        "cost_entry",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )
    op.create_index("ix_cost_entry_project_id_period", "cost_entry", ["project_id", "period"])
    op.create_index("ix_cost_entry_legal_entity_id", "cost_entry", ["legal_entity_id"])
    op.create_index("ix_cost_entry_source_id", "cost_entry", ["source_type", "source_id"])


def _rls_and_grants() -> None:
    for table in (
        "equipment_shift",
        "equipment_shift_line",
        "equipment_fuel",
        "equipment_delegate",
        "cost_entry",
    ):
        force_rls(table)

    # Лист: объект по project_ids; машинист - только свои листы; ввод - право write:shift; подтверждает
    # прораб объекта или делегат (проверка прав в триггере), правит подтверждённый - техдиректор.
    shift_scope = (
        f"equipment_shift.project_id = ANY {PROJECTS} AND {CAN_READ}"
        f" AND (NOT {IS_MACHINIST} OR {OWN_OPERATOR_OF_SHIFT})"
    )
    _policy("equipment_shift", "SELECT", shift_scope)
    _policy(
        "equipment_shift",
        "INSERT",
        f"equipment_shift.project_id = ANY {PROJECTS} AND {CAN_WRITE_SHIFT}"
        f" AND (NOT {IS_MACHINIST} OR {OWN_OPERATOR_OF_SHIFT})",
    )
    _policy(
        "equipment_shift",
        "UPDATE",
        f"{shift_scope} AND ((equipment_shift.created_by = app.user_id() AND {CAN_WRITE_SHIFT})"
        f" OR {CAN_APPROVE_SHIFT} OR {CAN_AFTER_APPROVAL}"
        f" OR app.equipment_delegated(equipment_shift.project_id, equipment_shift.work_date))",
    )

    # Строки листа видит тот, кто видит лист; машинист - строки своего листа (через оператора).
    _policy(
        "equipment_shift_line",
        "SELECT",
        f"equipment_shift_line.project_id = ANY {PROJECTS} AND {CAN_READ}"
        f" AND (NOT {IS_MACHINIST} OR EXISTS (SELECT 1 FROM public.equipment_shift s"
        " JOIN public.equipment_operator o ON o.id = s.operator_id"
        " WHERE s.id = equipment_shift_line.shift_id AND o.user_id = app.user_id()))",
    )
    _policy(
        "equipment_shift_line",
        "INSERT",
        f"equipment_shift_line.project_id = ANY {PROJECTS} AND {CAN_WRITE_SHIFT}",
    )
    _policy(
        "equipment_shift_line",
        "UPDATE",
        f"equipment_shift_line.project_id = ANY {PROJECTS}"
        f" AND ({CAN_WRITE_SHIFT} OR {CAN_AFTER_APPROVAL})",
    )

    # ГСМ: в пределах юрлицы (накладные и объектные строки); машинист видит свои заправки.
    fuel_scope = (
        f"equipment_fuel.legal_entity_id = ANY {LEGAL}"
        f" AND (equipment_fuel.project_id IS NULL OR equipment_fuel.project_id = ANY {PROJECTS})"
    )
    _policy(
        "equipment_fuel",
        "SELECT",
        f"equipment_fuel.created_by = app.user_id() OR ({fuel_scope} AND {CAN_READ}"
        f" AND NOT {IS_MACHINIST})",
    )
    _policy("equipment_fuel", "INSERT", f"{fuel_scope} AND {CAN_WRITE_SHIFT}")
    _policy("equipment_fuel", "UPDATE", f"{fuel_scope} AND {CAN_AFTER_APPROVAL}")

    # Делегаты: видит сам делегат и все, кто видит объект; создаёт и отзывает утверждающий объекта.
    _policy(
        "equipment_delegate",
        "SELECT",
        f"equipment_delegate.delegate_user_id = app.user_id()"
        f" OR (equipment_delegate.project_id = ANY {PROJECTS} AND {CAN_READ})",
    )
    _policy(
        "equipment_delegate",
        "INSERT",
        f"equipment_delegate.project_id = ANY {PROJECTS} AND {CAN_APPROVE_SHIFT}"
        " AND equipment_delegate.created_by = app.user_id()",
    )
    _policy(
        "equipment_delegate",
        "UPDATE",
        f"equipment_delegate.project_id = ANY {PROJECTS} AND {CAN_APPROVE_SHIFT}",
    )

    # Журнал затрат: только с гейтом денег и в пределах юрлица (как ставки в SE-1); накладные (без
    # объекта) - только с гейтом. Запись - через определители (kut_auth, BYPASSRLS); прямой записи у
    # приложения нет ни правами, ни политикой.
    _policy(
        "cost_entry",
        "SELECT",
        f"{INTERNAL} AND {CAN_READ_RATES} AND cost_entry.legal_entity_id = ANY {LEGAL}"
        " AND (cost_entry.project_id IS NULL OR cost_entry.project_id = ANY " + PROJECTS + ")",
    )
    _policy("cost_entry", "INSERT", "false")
    _policy("cost_entry", "UPDATE", "false")

    grant("equipment_shift", app=READ_WRITE, worker=READ_WRITE)
    grant("equipment_shift_line", app=READ_WRITE, worker=READ_WRITE)
    grant("equipment_fuel", app=APPEND_ONLY, worker=APPEND_ONLY)
    grant("equipment_delegate", app=READ_WRITE, worker=READ_WRITE)
    grant("cost_entry", app="SELECT", worker="SELECT")
    # Определители (kut_auth, BYPASSRLS) читают новые таблицы и пишут только то, что им разрешено.
    op.execute(
        "GRANT SELECT ON equipment_shift, equipment_shift_line, equipment_fuel,"
        " equipment_delegate, cost_entry TO kut_auth"
    )
    op.execute("GRANT UPDATE (posting_version, lines_revision) ON equipment_shift TO kut_auth")
    op.execute("GRANT INSERT ON cost_entry TO kut_auth")
    op.execute(
        "GRANT UPDATE (reversed_at, reversed_by, reverse_reason) ON equipment_fuel TO kut_auth"
    )


def _audit() -> None:
    create_audit_triggers(
        "equipment_shift",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("legal_entity_id", "project_id", "equipment_unit_id", "created_by"),
    )
    create_audit_triggers(
        "equipment_shift_line",
        project_column="project_id",
        frozen_columns=("shift_id", "project_id"),
    )
    create_audit_triggers(
        "equipment_fuel",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "legal_entity_id",
            "project_id",
            "equipment_unit_id",
            "fuel_date",
            "liters",
            "amount",
            "source",
            "created_by",
        ),
    )
    create_audit_triggers(
        "equipment_delegate",
        project_column="project_id",
        frozen_columns=(
            "project_id",
            "delegate_user_id",
            "date_from",
            "date_to",
            "reason",
            "created_by",
        ),
    )
    create_audit_triggers(
        "cost_entry",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "legal_entity_id",
            "project_id",
            "node_id",
            "schedule_item_id",
            "equipment_unit_id",
            "period",
            "category",
            "source_type",
            "source_id",
            "posting_version",
            "line_no",
            "quantity",
            "tariff_unit",
            "rate",
            "amount",
            "reversal_of_id",
        ),
    )


def upgrade() -> None:
    _create_shift()
    _create_shift_line()
    _create_fuel()
    _create_delegate()
    _create_cost_entry()
    # Функцию создаёт и её права меняет владелец kut_auth: REVOKE от kut_owner без роли владельца отказывает.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(DELEGATED_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION app.equipment_delegated(uuid, date) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.equipment_delegated(uuid, date) TO kut_app, kut_worker"
    )
    op.execute("RESET ROLE")
    _rls_and_grants()
    _audit()


def downgrade() -> None:
    # Политики equipment_shift ссылаются на функцию: сначала таблицы (с политиками), потом функция.
    for table in ("cost_entry", "equipment_delegate", "equipment_fuel", "equipment_shift_line"):
        op.drop_table(table)
    op.drop_table("equipment_shift")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_delegated(uuid, date)")
    op.execute("RESET ROLE")
