"""документы: комментарии под деньгами; строка срока техники - по праву на вид (PO 2026-10-18)

Revision ID: c9e2d4b7a1f6
Revises: b4d8f2a6c9e1
Create Date: 2026-10-18 10:00:00.000000

Комментарий версии и комментарий события журнала статусов видят внутренние с правом на деньги вида: для
финансового документа - documents:read:financial, для денежных видов техники - equipment:read:rates, для
нефинансового - само право на документ. Размер, хэш, автор и дата видны по праву на документ, как раньше.
У kut_app отзывается SELECT на колонку comment: читать её можно только через функции app.version_comment и
app.event_comment (SECURITY DEFINER от kut_auth). Журнал аудита: строки версий и событий с непустым
комментарием видны по тому же праву (ограничивающая политика, как у сумм).

Строка срока техники (equipment_document): технический осмотр - по equipment:write:maintenance (механик), остальные
виды - по equipment:write:documents (техдиректор, начальник отдела). Проверка в политике вставки и в сервисе.
Строки срока не меняются (APPEND_ONLY): правило покрывает создание.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9e2d4b7a1f6"
down_revision: str | Sequence[str] | None = "b4d8f2a6c9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Колонки, которые kut_app читает напрямую: все, кроме comment (он читается через функции).
VERSION_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "deleted_at",
    "document_id",
    "project_id",
    "legal_entity_id",
    "version_no",
    "s3_key",
    "content_type",
    "size_bytes",
    "checksum_sha256",
    "status",
    "parent_version_id",
)
EVENT_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "deleted_at",
    "document_version_id",
    "project_id",
    "legal_entity_id",
    "from_status",
    "to_status",
    "actor_id",
)

INTERNAL = "(SELECT app.is_internal())"
LEGAL = "((SELECT app.legal_entity_ids())::uuid[])"
CAN_WRITE_MAINTENANCE = "(SELECT app.has_permission('equipment', 'write', 'maintenance'))"
CAN_WRITE_DOCUMENTS = "(SELECT app.has_permission('equipment', 'write', 'documents'))"

FUNCTIONS_NEW: tuple[str, ...] = (
    """
CREATE FUNCTION app.document_comment_visible(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.is_internal() AND app.document_allowed(d.id) AND (
        NOT d.financial OR CASE WHEN app.document_kind_money(d.kind)
            THEN app.has_permission('equipment', 'read', 'rates')
            ELSE app.has_permission('documents', 'read', 'financial') END), false)
    FROM public.document d WHERE d.id = p_document
$$
""",
    """
CREATE FUNCTION app.version_comment(p_version uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN app.document_comment_visible(v.document_id) THEN v.comment END
    FROM public.document_version v WHERE v.id = p_version
$$
""",
    """
CREATE FUNCTION app.event_comment(p_event uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN app.document_comment_visible(v.document_id) THEN e.comment END
    FROM public.document_status_event e
    JOIN public.document_version v ON v.id = e.document_version_id
    WHERE e.id = p_event
$$
""",
    """
CREATE FUNCTION app.audit_comment_visible(p_table text, p_data jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(app.document_comment_visible(CASE WHEN p_table = 'document_version'
        THEN (p_data ->> 'document_id')::uuid
        ELSE (SELECT v.document_id FROM public.document_version v
              WHERE v.id = (p_data ->> 'document_version_id')::uuid) END), false)
$$
""",
)

FUNCTION_SIGNATURES: tuple[str, ...] = (
    "document_comment_visible(uuid)",
    "version_comment(uuid)",
    "event_comment(uuid)",
    "audit_comment_visible(text, jsonb)",
)


def upgrade() -> None:
    # Функции принадлежат kut_auth: иначе выдача EXECUTE и REVOKE падают на прав владельца.
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS_NEW:
        op.execute(sql)
    for signature in FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_app, kut_auth")
    op.execute("RESET ROLE")

    # Колонка comment у kut_app: SELECT только по списку колонок (общий SELECT таблицы отзывается).
    op.execute("REVOKE SELECT ON document_version FROM kut_app")
    op.execute(f"GRANT SELECT ({', '.join(VERSION_COLUMNS)}) ON document_version TO kut_app")
    op.execute("REVOKE SELECT ON document_status_event FROM kut_app")
    op.execute(f"GRANT SELECT ({', '.join(EVENT_COLUMNS)}) ON document_status_event TO kut_app")

    # Журнал аудита: строка версии или события с комментарием - по праву на комментарий документа.
    op.execute(
        "CREATE POLICY audit_log_document_comment ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        " USING (COALESCE(table_name, '') NOT IN ('document_version', 'document_status_event')"
        " OR (COALESCE(new, old) ->> 'comment') IS NULL"
        " OR app.audit_comment_visible(table_name, COALESCE(new, old)))"
    )

    # Строка срока техники: механик вставляет только технический осмотр; остальные виды - по праву
    # загрузки техники (equipment:write:documents). Вторая проверка - в сервисе, с кодом ответа.
    op.execute("DROP POLICY equipment_document_insert_app ON equipment_document")
    op.execute(
        "CREATE POLICY equipment_document_insert_app ON equipment_document FOR INSERT TO kut_app"
        f" WITH CHECK ({INTERNAL} AND legal_entity_id = ANY {LEGAL} AND {CAN_WRITE_MAINTENANCE}"
        f" AND (kind = 'technical_inspection' OR {CAN_WRITE_DOCUMENTS}))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS equipment_document_insert_app ON equipment_document")
    op.execute(
        "CREATE POLICY equipment_document_insert_app ON equipment_document FOR INSERT TO kut_app"
        f" WITH CHECK ({INTERNAL} AND legal_entity_id = ANY {LEGAL} AND {CAN_WRITE_MAINTENANCE})"
    )
    op.execute("DROP POLICY IF EXISTS audit_log_document_comment ON audit_log")
    op.execute(f"REVOKE SELECT ({', '.join(VERSION_COLUMNS)}) ON document_version FROM kut_app")
    op.execute("GRANT SELECT ON document_version TO kut_app")
    op.execute(f"REVOKE SELECT ({', '.join(EVENT_COLUMNS)}) ON document_status_event FROM kut_app")
    op.execute("GRANT SELECT ON document_status_event TO kut_app")
    op.execute("SET LOCAL ROLE kut_auth")
    for signature in reversed(FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("RESET ROLE")
