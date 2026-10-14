"""документы: схема S5a - документ, версия, журнал статусов, связи, сумма, счётчик номеров (ADR-015)

Revision ID: 2633d20e6f23
Revises: 0bea6146f4c3
Create Date: 2026-10-14 11:00:00.000000

- document: шапка (вид, юрлицо, объект или юрлицо-область, контрагент, договор, регистрационный номер,
  текущая версия, архивная дата). Документ не удаляется; снятие с действия - archived_at.
  Виды и правила области - CHECK (список видов - app/modules/documents/types.py, литералы здесь).
- document_version: версия файла. Содержимое и номер версии неизменяемы (create_audit_triggers);
  статус меняет только определитель перехода (ревизия 3 этой серии), приложение UPDATE не получает.
- document_status_event: журнал переходов статуса (APPEND_ONLY, без UPDATE и DELETE).
- document_link: связи с узлом, работой, фактом, техникой; ровно одна цель. Связь доступа не даёт.
- document_amount: сумма финансового документа; RLS по documents:read:financial (как project_finance).
- document_number_counter: счётчик номеров без дыр; приложение таблицу не видит (меняет функция).
- legal_entity.code: короткий код юрлица для номера (<вид>-<код>-<год>-<n>), уникален среди живых.

Области видимости (project_id, legal_entity_id) денормализованы на дочерние таблицы: журнал аудита
берёт из них объект и юрлицо строки, как у остальных бизнес-таблиц.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "2633d20e6f23"
down_revision: str | Sequence[str] | None = "0bea6146f4c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "addendum",
    "commissioning_act",
    "contract_customer",
    "contract_rental",
    "contract_subcontract",
    "contract_supply",
    "equipment_insurance",
    "equipment_other",
    "equipment_passport",
    "equipment_permit",
    "equipment_technical_inspection",
    "estimate_local",
    "estimate_summary",
    "hidden_works_act",
    "invoice_esf",
    "ird",
    "ks2",
    "ks3",
    "ks6",
    "m7_act",
    "material_request",
    "waybill_internal",
    "waybill_m15",
    "waybill_torg12",
)
FINANCIAL = (
    "addendum",
    "contract_customer",
    "contract_rental",
    "contract_subcontract",
    "contract_supply",
    "estimate_local",
    "estimate_summary",
    "invoice_esf",
    "ks2",
    "ks3",
    "ks6",
    "waybill_m15",
    "waybill_torg12",
)
OBJECT = (
    "commissioning_act",
    "contract_customer",
    "contract_subcontract",
    "estimate_local",
    "estimate_summary",
    "hidden_works_act",
    "ird",
    "ks2",
    "ks3",
    "ks6",
    "material_request",
)
LEGAL = (
    "addendum",
    "contract_rental",
    "contract_supply",
    "equipment_insurance",
    "equipment_other",
    "equipment_passport",
    "equipment_permit",
    "equipment_technical_inspection",
    "invoice_esf",
)
COUNTERPARTY = (
    "contract_customer",
    "contract_rental",
    "contract_subcontract",
    "contract_supply",
    "invoice_esf",
    "ks2",
    "ks3",
    "ks6",
    "waybill_m15",
    "waybill_torg12",
)
CONTRACT = ("addendum", "contract_rental")
STATUSES = ("approved", "archived", "draft", "on_approval", "signed")
CONTENT_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/heic",
    "image/jpeg",
    "image/png",
    "image/webp",
)
MAX_FILE_BYTES = 30 * 1024 * 1024
PORTAL_BASE = ("id", "created_at", "updated_at", "deleted_at")  # мягкое удаление - фильтр ORM
PORTAL_DOCUMENT = (
    *PORTAL_BASE,
    "kind",
    "title",
    "project_id",
    "counterparty_id",
    "financial",
    "current_version_id",
)
PORTAL_VERSION = (*PORTAL_BASE, "document_id", "version_no", "status", "content_type", "size_bytes")
PORTAL_AMOUNT = (*PORTAL_BASE, "document_id", "amount")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _scope_check() -> str:
    return (
        f"CASE WHEN kind IN ({_in(OBJECT)}) THEN project_id IS NOT NULL"
        f" WHEN kind IN ({_in(LEGAL)}) THEN project_id IS NULL ELSE true END"
    )


def upgrade() -> None:
    op.add_column("legal_entity", sa.Column("code", sa.String(8), nullable=True))
    op.create_check_constraint(
        op.f("ck_legal_entity_code_valid"),
        "legal_entity",
        "code IS NULL OR code ~ '^[A-Za-z0-9А-Яа-яЁё]{1,8}$'",
    )
    op.create_index(
        "uq_legal_entity_code",
        "legal_entity",
        ["code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "document_number_counter",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_no", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_number_counter_legal_entity_id_legal_entity"),
        ),
        sa.CheckConstraint(
            f"kind IN ({_in(KINDS)})", name=op.f("ck_document_number_counter_kind_valid")
        ),
        sa.CheckConstraint(
            "last_no >= 0", name=op.f("ck_document_number_counter_last_no_not_negative")
        ),
        sa.PrimaryKeyConstraint(
            "legal_entity_id", "kind", "year", name=op.f("pk_document_number_counter")
        ),
    )

    business_table(
        "document",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
        sa.Column("contract_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("financial", sa.Boolean(), nullable=False),
        sa.Column("number_year", sa.Integer(), nullable=False),
        sa.Column("number_seq", sa.Integer(), nullable=False),
        sa.Column("registration_no", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_document_counterparty_id_counterparty"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["contract.id"], name=op.f("fk_document_contract_id_contract")
        ),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name=op.f("ck_document_kind_valid")),
        sa.CheckConstraint(
            f"financial = (kind IN ({_in(FINANCIAL)}))",
            name=op.f("ck_document_financial_matches_kind"),
        ),
        sa.CheckConstraint(_scope_check(), name=op.f("ck_document_scope_valid")),
        sa.CheckConstraint(
            f"CASE WHEN kind IN ({_in(COUNTERPARTY)}) THEN counterparty_id IS NOT NULL ELSE true END",
            name=op.f("ck_document_counterparty_required"),
        ),
        sa.CheckConstraint(
            f"CASE WHEN kind IN ({_in(CONTRACT)}) THEN contract_id IS NOT NULL ELSE true END",
            name=op.f("ck_document_contract_required"),
        ),
        sa.CheckConstraint("btrim(title) <> ''", name=op.f("ck_document_title_not_blank")),
        sa.CheckConstraint(
            "number_seq >= 1 AND number_year >= 2000", name=op.f("ck_document_number_valid")
        ),
    )
    op.create_index(op.f("ix_document_legal_entity_id"), "document", ["legal_entity_id"])
    op.create_index(op.f("ix_document_project_id"), "document", ["project_id"])
    op.create_index(op.f("ix_document_counterparty_id"), "document", ["counterparty_id"])
    op.create_index(op.f("ix_document_contract_id"), "document", ["contract_id"])
    op.create_index(
        "uq_document_legal_entity_id_registration_no",
        "document",
        ["legal_entity_id", "registration_no"],
        unique=True,
    )

    business_table(
        "document_version",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=True),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_document_version_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_version_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_version_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["document_version.id"],
            name=op.f("fk_document_version_parent_version_id_document_version"),
        ),
        sa.UniqueConstraint("s3_key", name=op.f("uq_document_version_s3_key")),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_document_version_version_positive")),
        sa.CheckConstraint(
            f"content_type IN ({_in(CONTENT_TYPES)})",
            name=op.f("ck_document_version_content_type_valid"),
        ),
        sa.CheckConstraint(
            f"size_bytes > 0 AND size_bytes <= {MAX_FILE_BYTES}",
            name=op.f("ck_document_version_size_valid"),
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_version_checksum_valid")
        ),
        sa.CheckConstraint(
            "position('/document/' || document_id::text || '/' IN s3_key) > 0",
            name=op.f("ck_document_version_key_in_document"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(STATUSES)})", name=op.f("ck_document_version_status_valid")
        ),
        sa.CheckConstraint(
            "comment IS NULL OR btrim(comment) <> ''",
            name=op.f("ck_document_version_comment_not_blank"),
        ),
    )
    op.create_index(op.f("ix_document_version_document_id"), "document_version", ["document_id"])
    op.create_index(
        op.f("ix_document_version_legal_entity_id"), "document_version", ["legal_entity_id"]
    )
    op.create_index(
        "uq_document_version_document_id_version_no",
        "document_version",
        ["document_id", "version_no"],
        unique=True,
    )
    op.create_foreign_key(
        op.f("fk_document_current_version_id_document_version"),
        "document",
        "document_version",
        ["current_version_id"],
        ["id"],
    )

    business_table(
        "document_status_event",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_version.id"],
            name=op.f("fk_document_status_event_document_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_status_event_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_status_event_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["user.id"], name=op.f("fk_document_status_event_actor_id_user")
        ),
        sa.CheckConstraint(
            f"to_status IN ({_in(STATUSES)})", name=op.f("ck_document_status_event_to_status_valid")
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_in(STATUSES)})",
            name=op.f("ck_document_status_event_from_status_valid"),
        ),
    )
    op.create_index(
        op.f("ix_document_status_event_document_version_id"),
        "document_status_event",
        ["document_version_id"],
    )
    op.create_index(
        op.f("ix_document_status_event_legal_entity_id"),
        "document_status_event",
        ["legal_entity_id"],
    )

    business_table(
        "document_link",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_node_id", sa.Uuid(), nullable=True),
        sa.Column("work_id", sa.Uuid(), nullable=True),
        sa.Column("work_actual_id", sa.Uuid(), nullable=True),
        sa.Column("equipment_unit_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_document_link_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_link_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_link_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["project_node_id"],
            ["project_node.id"],
            name=op.f("fk_document_link_project_node_id_project_node"),
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["work.id"], name=op.f("fk_document_link_work_id_work")
        ),
        sa.ForeignKeyConstraint(
            ["work_actual_id"],
            ["work_actual.id"],
            name=op.f("fk_document_link_work_actual_id_work_actual"),
        ),
        sa.ForeignKeyConstraint(
            ["equipment_unit_id"],
            ["equipment_unit.id"],
            name=op.f("fk_document_link_equipment_unit_id_equipment_unit"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(project_node_id, work_id, work_actual_id, equipment_unit_id) = 1",
            name=op.f("ck_document_link_one_target"),
        ),
    )
    op.create_index(op.f("ix_document_link_document_id"), "document_link", ["document_id"])
    op.create_index(op.f("ix_document_link_legal_entity_id"), "document_link", ["legal_entity_id"])

    business_table(
        "document_amount",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_document_amount_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_document_amount_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_document_amount_legal_entity_id_legal_entity"),
        ),
        sa.UniqueConstraint("document_id", name=op.f("uq_document_amount_document_id")),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_document_amount_amount_not_negative")),
    )
    op.create_index(
        op.f("ix_document_amount_legal_entity_id"), "document_amount", ["legal_entity_id"]
    )

    for table in (
        "document",
        "document_version",
        "document_status_event",
        "document_link",
        "document_amount",
        "document_number_counter",
    ):
        force_rls(table)

    # kut_worker (фоновые задачи, уведомления S6b) читает документы; записи у него нет.
    grant(
        "document",
        app="SELECT, INSERT, UPDATE (title, updated_at, updated_by)",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_DOCUMENT,
    )
    grant(
        "document_version",
        app="SELECT, INSERT",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_VERSION,
    )
    grant("document_status_event", app="SELECT", worker="SELECT")
    grant("document_link", app="SELECT, INSERT", worker="SELECT, INSERT")
    grant(
        "document_amount",
        app="SELECT, INSERT, UPDATE (amount, updated_at, updated_by)",
        worker="SELECT, INSERT",
        portal_columns=PORTAL_AMOUNT,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON document, document_version, document_amount TO kut_auth"
    )
    op.execute("GRANT SELECT, INSERT ON document_status_event, document_link TO kut_auth")
    op.execute("GRANT SELECT, INSERT, UPDATE ON document_number_counter TO kut_auth")

    create_audit_triggers(
        "document",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "kind",
            "legal_entity_id",
            "project_id",
            "counterparty_id",
            "contract_id",
            "financial",
            "number_year",
            "number_seq",
            "registration_no",
        ),
    )
    create_audit_triggers(
        "document_version",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "document_id",
            "version_no",
            "s3_key",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "comment",
            "parent_version_id",
        ),
    )
    create_audit_triggers(
        "document_status_event",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("document_version_id", "from_status", "to_status", "comment", "actor_id"),
    )
    create_audit_triggers(
        "document_link",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=(
            "document_id",
            "project_node_id",
            "work_id",
            "work_actual_id",
            "equipment_unit_id",
        ),
    )
    create_audit_triggers(
        "document_amount",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("document_id",),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_document_current_version_id_document_version"), "document", type_="foreignkey"
    )
    for table in (
        "document_amount",
        "document_link",
        "document_status_event",
        "document_version",
        "document",
    ):
        op.drop_table(table)
    op.drop_table("document_number_counter")
    op.drop_index("uq_legal_entity_code", table_name="legal_entity")
    op.drop_constraint(op.f("ck_legal_entity_code_valid"), "legal_entity", type_="check")
    op.drop_column("legal_entity", "code")
