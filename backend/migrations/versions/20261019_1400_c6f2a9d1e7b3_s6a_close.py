"""S6a-close: замещение не дольше 60 дней, видимость документа в функциях причины и маршрута, сквозная сумма и позиции подписанной версии

Revision ID: c6f2a9d1e7b3
Revises: b5e9c2a7d4f1
Create Date: 2026-10-19 14:00:00

Замещение: срок не дольше 60 дней (функция и CHECK таблицы). Причина решения и текущий шаг без доступа
к документу - NULL, в том числе для держателя права на деньги. Функция `app.version_source` даёт версию,
откуда берутся сумма и позиции: у подписанной версии - родительская согласованная (сквозная сумма, PO
2026-10-19). Заказчик видит позиции и суммы родителя через подписанную версию: политики портала для строк,
цен и сумм версии берут видимость через `app.portal_version_visible`, которая повторяет правило видимости
версий портала (согласованная или подписанная и доступный документ). Гард R1 не меняется.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6f2a9d1e7b3"
down_revision: str | Sequence[str] | None = "b5e9c2a7d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGINS = "kut_app, kut_portal, kut_worker"

PORTAL_POLICIES = (
    ("document_line", "document_line_select_portal"),
    ("document_line_price", "document_line_price_select_portal"),
    ("document_version_amount", "document_version_amount_select_portal"),
)

DELEGATION_PRECHECK = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.document_delegation WHERE valid_to - valid_from > 60) THEN
        RAISE EXCEPTION 'approval.period_too_long: есть замещения дольше 60 дней, миграция остановлена, данные не изменены';
    END IF;
END $$
"""

DELEGATION_CREATE = """
CREATE OR REPLACE FUNCTION app.delegation_create(p_delegate uuid, p_role text, p_from date, p_to date)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := app.user_id();
    newid uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    IF app.user_role_code() IS DISTINCT FROM p_role THEN
        RAISE EXCEPTION 'approval.delegation_role_invalid';
    END IF;
    IF p_delegate = actor THEN
        RAISE EXCEPTION 'approval.delegation_self';
    END IF;
    IF p_from < app.local_today() THEN
        RAISE EXCEPTION 'approval.period_in_past';
    END IF;
    IF p_to < p_from THEN
        RAISE EXCEPTION 'approval.period_invalid';
    END IF;
    IF p_to - p_from > 60 THEN
        RAISE EXCEPTION 'approval.period_too_long';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public."user" u WHERE u.id = p_delegate AND u.is_active) THEN
        RAISE EXCEPTION 'approval.delegate_not_found';
    END IF;
    INSERT INTO public.document_delegation (delegator_id, delegate_id, role_code, valid_from, valid_to)
    VALUES (actor, p_delegate, p_role, p_from, p_to)
    RETURNING id INTO newid;
    RETURN newid;
END $$
"""

DELEGATION_CREATE_OLD = DELEGATION_CREATE.replace(
    "    IF p_to - p_from > 60 THEN\n        RAISE EXCEPTION 'approval.period_too_long';\n    END IF;\n",
    "",
)

ROUTE_CURRENT_POSITION = """
CREATE OR REPLACE FUNCTION app.route_current_position(p_version uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT min(s.position) FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.status = 'pending'
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version)
      AND EXISTS (SELECT 1 FROM public.document_version dv
                  WHERE dv.id = p_version AND app.document_allowed(dv.document_id))
$$
"""

ROUTE_CURRENT_POSITION_OLD = """
CREATE OR REPLACE FUNCTION app.route_current_position(p_version uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT min(s.position) FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.status = 'pending'
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version)
$$
"""

DECISION_REASON = """
CREATE OR REPLACE FUNCTION app.decision_reason(p_decision uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    dec public.approval_decision;
    doc public.document;
    v public.document_version;
    visible boolean;
BEGIN
    SELECT * INTO dec FROM public.approval_decision WHERE id = p_decision;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT * INTO doc FROM public.document WHERE id = dec.document_id;
    IF NOT app.document_allowed(doc.id) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = dec.version_id;
    IF NOT doc.financial THEN
        visible := true;
    ELSE
        visible := app.document_money_right(doc.id)
            OR v.created_by = app.user_id()
            OR app.route_participant(v.id);
    END IF;
    RETURN CASE WHEN coalesce(visible, false) THEN dec.reason END;
END $$
"""

DECISION_REASON_OLD = """
CREATE OR REPLACE FUNCTION app.decision_reason(p_decision uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    dec public.approval_decision;
    doc public.document;
    v public.document_version;
    visible boolean;
BEGIN
    SELECT * INTO dec FROM public.approval_decision WHERE id = p_decision;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT * INTO doc FROM public.document WHERE id = dec.document_id;
    SELECT * INTO v FROM public.document_version WHERE id = dec.version_id;
    IF NOT doc.financial THEN
        visible := app.document_allowed(doc.id);
    ELSE
        visible := app.document_money_right(doc.id)
            OR v.created_by = app.user_id()
            OR app.route_participant(v.id);
    END IF;
    RETURN CASE WHEN coalesce(visible, false) THEN dec.reason END;
END $$
"""

VERSION_SOURCE = """
CREATE FUNCTION app.version_source(p_version uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE(v.parent_version_id, v.id) FROM public.document_version v
    WHERE v.id = p_version AND app.document_allowed(v.document_id)
$$
"""

VERSION_SOURCE_NO = """
CREATE FUNCTION app.version_source_no(p_version uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT s.version_no FROM public.document_version v
    JOIN public.document_version s ON s.id = COALESCE(v.parent_version_id, v.id)
    WHERE v.id = p_version AND app.document_allowed(v.document_id)
$$
"""

PORTAL_VERSION_VISIBLE = """
CREATE FUNCTION app.portal_version_visible(p_version uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM public.document_version v
                   WHERE (v.id = p_version OR v.parent_version_id = p_version)
                     AND v.status IN ('approved', 'signed')
                     AND app.document_allowed(v.document_id))
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(DELEGATION_PRECHECK)
    op.execute("RESET ROLE")
    op.create_check_constraint(
        op.f("ck_document_delegation_max_period"),
        "document_delegation",
        sa.text("valid_to - valid_from <= 60"),
    )
    op.create_index(
        op.f("ix_document_version_parent_version_id"),
        "document_version",
        ["parent_version_id"],
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(DELEGATION_CREATE)
    op.execute(ROUTE_CURRENT_POSITION)
    op.execute(DECISION_REASON)
    op.execute(VERSION_SOURCE)
    op.execute(VERSION_SOURCE_NO)
    op.execute(PORTAL_VERSION_VISIBLE)
    op.execute("REVOKE ALL ON FUNCTION app.version_source(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.version_source(uuid) TO {LOGINS}")
    op.execute("REVOKE ALL ON FUNCTION app.version_source_no(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.version_source_no(uuid) TO {LOGINS}")
    op.execute("REVOKE ALL ON FUNCTION app.portal_version_visible(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.portal_version_visible(uuid) TO {LOGINS}")
    op.execute("RESET ROLE")
    for table, policy in PORTAL_POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR SELECT TO kut_portal"
            " USING (app.portal_version_visible(version_id))"
        )


def downgrade() -> None:
    for table, policy in PORTAL_POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR SELECT TO kut_portal"
            " USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"
        )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.portal_version_visible(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.version_source_no(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.version_source(uuid)")
    op.execute(DELEGATION_CREATE_OLD)
    op.execute(ROUTE_CURRENT_POSITION_OLD)
    op.execute(DECISION_REASON_OLD)
    op.execute("RESET ROLE")
    op.drop_index(op.f("ix_document_version_parent_version_id"), table_name="document_version")
    op.drop_constraint(
        op.f("ck_document_delegation_max_period"), "document_delegation", type_="check"
    )
