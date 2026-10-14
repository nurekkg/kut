"""документы: ужесточение после ревью безопасности S5a (NULL в правилах видимости, переход статуса)

Revision ID: d93871fbc549
Revises: fa1e8c2d5b93
Create Date: 2026-10-15 10:00:00.000000

Ревью безопасности S5a (2026-10-15):
- app.document_allowed (внутренняя ветка) возвращала NULL, когда у документа юрлица пустой набор
  объектов пользователя: NULL в IF не срабатывает, и выдача ключа файла (document_version_storage_key)
  шла бы дальше. Теперь функция возвращает только true или false (COALESCE), а выдача ключа
  проверяет результат тоже через COALESCE. Сейчас путь через API закрыт выборкой под RLS; это защита
  в глубину.
- app.document_version_transition (переход статуса версии) больше не доступен kut_app: приложение
  не имеет пути к системной сессии, а сид и тесты переходят через kut_worker. Пользовательские
  переходы появятся в S6a, тогда выдача будет с проверкой пользователя.

Откат возвращает прежние определения функций и прежние гранты.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d93871fbc549"
down_revision: str | Sequence[str] | None = "fa1e8c2d5b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CUSTOMER_KINDS = (
    "'commissioning_act', 'contract_customer', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
)
SUBCONTRACTOR_KINDS = "'contract_subcontract', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
SUPPLIER_KINDS = (
    "'contract_supply', 'invoice_esf', 'material_request', 'waybill_m15', 'waybill_torg12'"
)

ALLOWED_NEW = f"""
CREATE OR REPLACE FUNCTION app.document_allowed(p_document uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = p_document;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF app.is_internal() THEN
        RETURN COALESCE(app.has_permission('documents', 'read') AND (
            d.project_id = ANY(app.project_ids())
            OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids()))), false);
    END IF;
    IF NOT app.has_permission('documents', 'read') OR d.archived_at IS NOT NULL
       OR d.counterparty_id IS NULL OR d.counterparty_id IS DISTINCT FROM app.counterparty_id()
       OR (d.project_id IS NOT NULL AND NOT d.project_id = ANY(app.project_ids())) THEN
        RETURN false;
    END IF;
    IF NOT (
        (app.user_role_code() = 'customer' AND d.kind IN ({CUSTOMER_KINDS}))
        OR (app.user_role_code() = 'subcontractor' AND d.kind IN ({SUBCONTRACTOR_KINDS}))
        OR (app.user_role_code() = 'supplier' AND d.kind IN ({SUPPLIER_KINDS}))) THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = d.current_version_id AND v.status IN ('approved', 'signed'));
END $$
"""

ALLOWED_OLD = f"""
CREATE OR REPLACE FUNCTION app.document_allowed(p_document uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = p_document;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF app.is_internal() THEN
        RETURN app.has_permission('documents', 'read') AND (
            d.project_id = ANY(app.project_ids())
            OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids())));
    END IF;
    IF NOT app.has_permission('documents', 'read') OR d.archived_at IS NOT NULL
       OR d.counterparty_id IS NULL OR d.counterparty_id IS DISTINCT FROM app.counterparty_id()
       OR (d.project_id IS NOT NULL AND NOT d.project_id = ANY(app.project_ids())) THEN
        RETURN false;
    END IF;
    IF NOT (
        (app.user_role_code() = 'customer' AND d.kind IN ({CUSTOMER_KINDS}))
        OR (app.user_role_code() = 'subcontractor' AND d.kind IN ({SUBCONTRACTOR_KINDS}))
        OR (app.user_role_code() = 'supplier' AND d.kind IN ({SUPPLIER_KINDS}))) THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = d.current_version_id AND v.status IN ('approved', 'signed'));
END $$
"""

STORAGE_KEY_NEW = """
CREATE OR REPLACE FUNCTION app.document_version_storage_key(p_version uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR NOT COALESCE(app.document_allowed(v.document_id), false) THEN
        RETURN NULL;
    END IF;
    IF NOT app.is_internal() AND v.status NOT IN ('approved', 'signed') THEN
        RETURN NULL;
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF app.is_internal() AND d.financial AND NOT app.has_permission('documents', 'read', 'financial') THEN
        RETURN NULL;
    END IF;
    RETURN v.s3_key;
END $$
"""

STORAGE_KEY_OLD = """
CREATE OR REPLACE FUNCTION app.document_version_storage_key(p_version uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR NOT app.document_allowed(v.document_id) THEN
        RETURN NULL;
    END IF;
    IF NOT app.is_internal() AND v.status NOT IN ('approved', 'signed') THEN
        RETURN NULL;
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF app.is_internal() AND d.financial AND NOT app.has_permission('documents', 'read', 'financial') THEN
        RETURN NULL;
    END IF;
    RETURN v.s3_key;
END $$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(ALLOWED_NEW)
    op.execute(STORAGE_KEY_NEW)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION app.document_version_transition(uuid, text, text) FROM kut_app"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(ALLOWED_OLD)
    op.execute(STORAGE_KEY_OLD)
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.document_version_transition(uuid, text, text) TO kut_app"
    )
    op.execute("RESET ROLE")
