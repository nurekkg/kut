"""аудит документов: строки видны по праву на документ; ключ файла и хэш - не в аудите (C1, S6a-1)

Revision ID: d7e3a1c9b5f2
Revises: c9e2d4b7a1f6
Create Date: 2026-10-18 11:00:00.000000

Журнал аудита читает любой внутренний пользователь с доступом к объекту или юрлицу (политика
audit_log_select_app). Строки таблиц документов (шапка, версии, связи, события) ему при этом видны без права на
документ: название, контрагент, регистрационный номер, ключ файла и хэш. Ограничивающая политика отдаёт строку
таблицы документа только при app.document_allowed документа. В аудит версий не пишутся s3_key и checksum_sha256
(hidden_columns): триггеры аудита таблицы document_version пересоздаются. Исторические строки аудита не
переписываются (журнал append-only); их очистка - решение PO.
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_audit_triggers, drop_audit_triggers

revision: str = "d7e3a1c9b5f2"
down_revision: str | Sequence[str] | None = "c9e2d4b7a1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Таблицы документов, чьи строки аудита видны только при праве на документ.
DOCUMENT_AUDIT_TABLES = (
    "document",
    "document_version",
    "document_link",
    "document_status_event",
    "document_version_amount",
)

VERSION_FROZEN = (
    "document_id",
    "version_no",
    "s3_key",
    "content_type",
    "size_bytes",
    "checksum_sha256",
    "comment",
    "parent_version_id",
)

FUNCTION_NEW = """
CREATE FUNCTION app.audit_document_visible(p_table text, p_row uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    RETURN COALESCE(app.document_allowed(CASE p_table
        WHEN 'document' THEN p_row
        WHEN 'document_version' THEN (SELECT x.document_id FROM public.document_version x WHERE x.id = p_row)
        WHEN 'document_link' THEN (SELECT x.document_id FROM public.document_link x WHERE x.id = p_row)
        WHEN 'document_status_event' THEN (
            SELECT v.document_id FROM public.document_status_event e
            JOIN public.document_version v ON v.id = e.document_version_id WHERE e.id = p_row)
        WHEN 'document_version_amount' THEN (
            SELECT x.document_id FROM public.document_version_amount x WHERE x.id = p_row)
        WHEN 'document_line' THEN (SELECT x.document_id FROM public.document_line x WHERE x.id = p_row)
        WHEN 'document_line_price' THEN (
            SELECT x.document_id FROM public.document_line_price x WHERE x.id = p_row)
    END), false);
END
$$
"""


def upgrade() -> None:
    drop_audit_triggers("document_version")
    create_audit_triggers(
        "document_version",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        hidden_columns=("s3_key", "checksum_sha256"),
        frozen_columns=VERSION_FROZEN,
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(FUNCTION_NEW)
    op.execute("REVOKE ALL ON FUNCTION app.audit_document_visible(text, uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.audit_document_visible(text, uuid) TO kut_app, kut_auth"
    )
    op.execute("RESET ROLE")
    tables = ", ".join(f"'{name}'" for name in DOCUMENT_AUDIT_TABLES)
    op.execute(
        "CREATE POLICY audit_log_document_access ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        f" USING (COALESCE(table_name, '') NOT IN ({tables})"
        " OR app.audit_document_visible(table_name, row_id))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_log_document_access ON audit_log")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.audit_document_visible(text, uuid)")
    op.execute("RESET ROLE")
    drop_audit_triggers("document_version")
    create_audit_triggers(
        "document_version",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=VERSION_FROZEN,
    )
