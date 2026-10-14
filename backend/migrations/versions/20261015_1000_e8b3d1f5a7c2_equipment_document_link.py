"""техника: связь строки срока с документом S5a (ADR-015 часть 3, Q8)

Revision ID: e8b3d1f5a7c2
Revises: d93871fbc549
Create Date: 2026-10-15 10:00:00.000000

Строка срока документа техники (equipment_document) получает необязательную ссылку на документ
реестра S5a. Таблица только для вставки, права колонки наследуются от таблицы (APPEND_ONLY),
RLS не меняется. Вид документа и его связь с единицей проверяет сервис техники: для этого нужна
таблица document_link, которую CHECK таблицы не видит.
Откат убирает колонку вместе со ссылкой; строки срока остаются.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3d1f5a7c2"
down_revision: str | Sequence[str] | None = "d93871fbc549"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("equipment_document", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_equipment_document_document_id_document"),
        "equipment_document",
        "document",
        ["document_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_equipment_document_document_id"), "equipment_document", ["document_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_equipment_document_document_id"), table_name="equipment_document")
    op.drop_constraint(
        op.f("fk_equipment_document_document_id_document"),
        "equipment_document",
        type_="foreignkey",
    )
    op.drop_column("equipment_document", "document_id")
