"""identity schema: org, rbac, identity, project tables

Revision ID: 738b6eb6ef1b
Revises: bdb7518b9492
Create Date: 2026-10-01 09:32:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "738b6eb6ef1b"
down_revision: str | Sequence[str] | None = "bdb7518b9492"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Порядок важен только для downgrade: потомки раньше родителей.
BUSINESS_TABLES = (
    "legal_entity",
    "counterparty",
    "role",
    "permission",
    "role_permission",
    "user",
    "project",
    "user_project_access",
    "user_legal_entity_access",
)


def _business_table(name: str, *items: sa.schema.SchemaItem) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *items,
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    )


def upgrade() -> None:
    _business_table(
        "legal_entity",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("tax_id", sa.String(), nullable=True),
    )

    _business_table(
        "counterparty",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("tax_id", sa.String(), nullable=True),
        sa.Column("types", postgresql.ARRAY(sa.String()), nullable=False),
        sa.CheckConstraint(
            "cardinality(types) >= 1 AND types <@ ARRAY['customer', 'subcontractor', 'supplier',"
            " 'investor']::varchar[]",
            name=op.f("ck_counterparty_types_valid"),
        ),
    )
    op.create_index("ix_counterparty_types", "counterparty", ["types"], postgresql_using="gin")

    _business_table(
        "role",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("requires_2fa", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rank", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(
            "scope IN ('group', 'project', 'counterparty')", name=op.f("ck_role_scope_valid")
        ),
        sa.UniqueConstraint("code", name=op.f("uq_role_code")),
    )

    _business_table(
        "permission",
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("qualifier", sa.String(), nullable=True),
        sa.UniqueConstraint(
            "module",
            "action",
            "qualifier",
            name="uq_permission_module_action_qualifier",
            postgresql_nulls_not_distinct=True,
        ),
    )

    _business_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name=op.f("fk_role_permission_role_id_role")
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name=op.f("fk_role_permission_permission_id_permission"),
        ),
    )
    op.create_index(
        "ix_role_permission_permission_id", "role_permission", ["permission_id"], unique=False
    )
    op.create_index(
        "uq_role_permission_role_id_permission_id",
        "role_permission",
        ["role_id", "permission_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    _business_table(
        "user",
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("role_ver", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "phone IS NOT NULL OR email IS NOT NULL", name=op.f("ck_user_contact_required")
        ),
        sa.CheckConstraint(r"phone ~ '^\+[1-9][0-9]{6,14}$'", name=op.f("ck_user_phone_e164")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_user_email_lowercase")),
        sa.ForeignKeyConstraint(["role_id"], ["role.id"], name=op.f("fk_user_role_id_role")),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_user_counterparty_id_counterparty"),
        ),
    )
    op.create_index("ix_user_role_id", "user", ["role_id"], unique=False)
    op.create_index("ix_user_counterparty_id", "user", ["counterparty_id"], unique=False)
    op.create_index(
        "uq_user_email",
        "user",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_user_phone",
        "user",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    _business_table(
        "project",
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'completed', 'archived')",
            name=op.f("ck_project_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_project_legal_entity_id_legal_entity"),
        ),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_project_id_legal_entity_id"),
    )
    op.create_index("ix_project_legal_entity_id", "project", ["legal_entity_id"], unique=False)

    _business_table(
        "user_project_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_project_access_user_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_user_project_access_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name=op.f("fk_user_project_access_role_id_role")
        ),
    )
    op.create_index(
        "ix_user_project_access_project_id", "user_project_access", ["project_id"], unique=False
    )
    op.create_index(
        "ix_user_project_access_role_id", "user_project_access", ["role_id"], unique=False
    )
    op.create_index(
        "uq_user_project_access_user_id_project_id",
        "user_project_access",
        ["user_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    _business_table(
        "user_legal_entity_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_legal_entity_access_user_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_user_legal_entity_access_legal_entity_id_legal_entity"),
        ),
    )
    op.create_index(
        "ix_user_legal_entity_access_legal_entity_id",
        "user_legal_entity_access",
        ["legal_entity_id"],
        unique=False,
    )
    op.create_index(
        "uq_user_legal_entity_access_user_id_legal_entity_id",
        "user_legal_entity_access",
        ["user_id", "legal_entity_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # created_by / updated_by -> user.id после всех таблиц: user и role ссылаются друг на друга.
    for table in BUSINESS_TABLES:
        for column in ("created_by", "updated_by"):
            op.create_foreign_key(
                op.f(f"fk_{table}_{column}_user"), table, "user", [column], ["id"]
            )


def downgrade() -> None:
    for table in BUSINESS_TABLES:
        for column in ("created_by", "updated_by"):
            op.drop_constraint(op.f(f"fk_{table}_{column}_user"), table, type_="foreignkey")
    for table in reversed(BUSINESS_TABLES):
        op.drop_table(table)
