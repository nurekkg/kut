"""позиции версии: правка объёмов прорабом, очистка цены, гарды архива и удаления с ценой (S6a-2, шаг 0)

Revision ID: a8d3f1c6e4b2
Revises: e4a8c2d6f1b3
Create Date: 2026-10-19 10:00:00.000000

Прораб вносит объёмы КС-2 (решение PO 2026-10-19): правит наименование, единицу и количество позиции в черновике.
Позиция остаётся той же строкой, поэтому её цена на месте; замена содержимого - UPDATE, не новая строка.
Номер позиции, версия и документ неизменяемы (колонки в frozen_columns). Удалять позицию с ценой может только
держатель права на деньги вида (`line.priced_delete_forbidden`). Очистка цены - мягкое удаление строки цены
(deleted_at), тем же правом. Гарды позиций и цен отказывают в архивном документе (`document.archived`).
Представление `document_line_amount` не учитывает очищенные цены.
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_audit_triggers, drop_audit_triggers

revision: str = "a8d3f1c6e4b2"
down_revision: str | Sequence[str] | None = "e4a8c2d6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Колонки, которые правит прораб в черновике: содержимое позиции. Остальное неизменяемо.
LINE_FROZEN_V2 = ("version_id", "document_id", "legal_entity_id", "project_id", "position_no")
LINE_FROZEN_V1 = (
    "version_id",
    "document_id",
    "legal_entity_id",
    "project_id",
    "position_no",
    "name",
    "unit",
    "quantity",
    "work_id",
    "project_node_id",
)

LINE_GUARD_NEW = """
CREATE OR REPLACE FUNCTION app.document_line_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND OR v.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_kind_lines(d.kind) THEN
        RAISE EXCEPTION 'line.kind_not_allowed';
    END IF;
    IF NEW.project_node_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project_node n WHERE n.id = NEW.project_node_id AND n.project_id = d.project_id
    ) THEN
        RAISE EXCEPTION 'line.node_invalid';
    END IF;
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.document_line_price p
                   WHERE p.line_id = NEW.id AND p.deleted_at IS NULL)
       AND NOT app.document_money_right(NEW.document_id) THEN
        RAISE EXCEPTION 'line.priced_delete_forbidden';
    END IF;
    RETURN NEW;
END $$
"""

PRICE_GUARD_NEW = """
CREATE OR REPLACE FUNCTION app.document_line_price_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    l public.document_line;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_price_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO l FROM public.document_line WHERE id = NEW.line_id;
    IF NOT FOUND OR l.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'line.deleted';
    END IF;
    IF l.version_id IS DISTINCT FROM NEW.version_id OR l.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    RETURN NEW;
END $$
"""

VIEW_NEW = (
    "CREATE OR REPLACE VIEW document_line_amount WITH (security_invoker = true) AS"
    " SELECT l.id AS line_id, l.version_id, l.document_id, l.position_no, l.quantity,"
    " p.unit_price, round(l.quantity * p.unit_price, 2) AS amount"
    " FROM document_line l LEFT JOIN document_line_price p ON p.line_id = l.id AND p.deleted_at IS NULL"
    " WHERE l.deleted_at IS NULL"
)
VIEW_OLD = (
    "CREATE OR REPLACE VIEW document_line_amount WITH (security_invoker = true) AS"
    " SELECT l.id AS line_id, l.version_id, l.document_id, l.position_no, l.quantity,"
    " p.unit_price, round(l.quantity * p.unit_price, 2) AS amount"
    " FROM document_line l LEFT JOIN document_line_price p ON p.line_id = l.id"
    " WHERE l.deleted_at IS NULL"
)

LINE_GUARD_OLD = """
CREATE OR REPLACE FUNCTION app.document_line_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND OR v.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF NOT app.document_kind_lines(d.kind) THEN
        RAISE EXCEPTION 'line.kind_not_allowed';
    END IF;
    IF NEW.project_node_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project_node n WHERE n.id = NEW.project_node_id AND n.project_id = d.project_id
    ) THEN
        RAISE EXCEPTION 'line.node_invalid';
    END IF;
    RETURN NEW;
END $$
"""

PRICE_GUARD_OLD = """
CREATE OR REPLACE FUNCTION app.document_line_price_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    l public.document_line;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'line.delete_forbidden';
    END IF;
    IF app.user_id() IS NOT NULL AND NOT app.line_price_writable(NEW.document_id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT * INTO l FROM public.document_line WHERE id = NEW.line_id;
    IF NOT FOUND OR l.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'line.deleted';
    END IF;
    IF l.version_id IS DISTINCT FROM NEW.version_id OR l.document_id IS DISTINCT FROM NEW.document_id THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'line.version_not_draft';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(LINE_GUARD_NEW)
    op.execute(PRICE_GUARD_NEW)
    op.execute("RESET ROLE")
    drop_audit_triggers("document_line")
    create_audit_triggers(
        "document_line",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=LINE_FROZEN_V2,
    )
    op.execute(
        "GRANT UPDATE (name, unit, quantity, work_id, project_node_id) ON document_line TO kut_app"
    )
    op.execute("GRANT UPDATE (deleted_at) ON document_line_price TO kut_app")
    op.execute(VIEW_NEW)  # представление принадлежит kut_owner: заменяет миграция, не функция
    op.execute("GRANT SELECT ON document_line_amount TO kut_app, kut_portal")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(LINE_GUARD_OLD)
    op.execute(PRICE_GUARD_OLD)
    op.execute("RESET ROLE")
    op.execute(VIEW_OLD)
    op.execute("REVOKE UPDATE (deleted_at) ON document_line_price FROM kut_app")
    op.execute(
        "REVOKE UPDATE (name, unit, quantity, work_id, project_node_id) ON document_line FROM kut_app"
    )
    drop_audit_triggers("document_line")
    create_audit_triggers(
        "document_line",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=LINE_FROZEN_V1,
    )
