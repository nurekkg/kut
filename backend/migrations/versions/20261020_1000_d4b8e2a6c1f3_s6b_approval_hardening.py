"""S6b, шаг 0: замещение до 60 дней включительно, запрет пересечений и не-внутреннего делегата,
детерминированный выбор делегатора, имена решивших для внутренних, переход версии для worker

Revision ID: d4b8e2a6c1f3
Revises: c6f2a9d1e7b3
Create Date: 2026-10-20 10:00:00

Замещение: разность дат не больше 59 (60 календарных дней включительно). Пересечение записей одного
делегирующего по одной роли - 409 approval.delegation_overlap (гард BEFORE INSERT с блокировкой по паре).
Делегат - только внутренний пользователь (роль не со scope counterparty). route_check берёт делегатора
по порядку (valid_from, id). app.document_person_names - имена авторов и решивших для внутреннего, который
видит документ; портал получает пусто. kut_worker переводит версии через app.version_move, а не через
app.document_version_transition (EXECUTE отозван).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8e2a6c1f3"
down_revision: str | Sequence[str] | None = "c6f2a9d1e7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DELEGATION_PRECHECK = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.document_delegation WHERE valid_to - valid_from > 59) THEN
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
    IF p_to - p_from > 59 THEN
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

DELEGATION_CREATE_PREVIOUS = DELEGATION_CREATE.replace("> 59", "> 60")

DELEGATION_INSERT_GUARD = """
CREATE FUNCTION app.delegation_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public."user" u JOIN public.role r ON r.id = u.role_id
                   WHERE u.id = NEW.delegate_id AND u.deleted_at IS NULL
                     AND r.scope <> 'counterparty') THEN
        RAISE EXCEPTION 'approval.delegate_not_internal';
    END IF;
    -- Не больше 20 созданий за сутки на делегирующего (отозванные тоже считаются): иначе цикл
    -- «создать - отозвать» плодит записи без предела. Блокировка по делегирующему - против гонки.
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.delegator_id::text, 1));
    IF (SELECT count(*) FROM public.document_delegation d
        WHERE d.delegator_id = NEW.delegator_id
          AND d.created_at > now() - interval '1 day') >= 20 THEN
        RAISE EXCEPTION 'approval.delegation_limit';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.delegator_id::text || ':' || NEW.role_code, 0));
    IF EXISTS (SELECT 1 FROM public.document_delegation d
               WHERE d.delegator_id = NEW.delegator_id AND d.role_code = NEW.role_code
                 AND d.revoked_at IS NULL AND d.deleted_at IS NULL
                 AND NEW.valid_from <= d.valid_to AND d.valid_from <= NEW.valid_to) THEN
        RAISE EXCEPTION 'approval.delegation_overlap';
    END IF;
    RETURN NEW;
END $$
"""

ROUTE_CHECK = """
CREATE OR REPLACE FUNCTION app.route_check(p_version uuid, p_position integer, p_decision text, p_reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := app.user_id();
    v public.document_version;
    d public.document;
    st public.version_route_step;
    deleg uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    IF p_decision NOT IN ('approve', 'reject') THEN
        RAISE EXCEPTION 'approval.decision_invalid';
    END IF;
    IF p_decision = 'reject' AND btrim(coalesce(p_reason, '')) = '' THEN
        RAISE EXCEPTION 'approval.reason_required';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'on_approval' THEN
        RAISE EXCEPTION 'approval.version_not_on_approval';
    END IF;
    SELECT * INTO st FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.position = p_position
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version);
    IF NOT FOUND OR st.status <> 'pending'
       OR p_position IS DISTINCT FROM app.route_current_position(p_version) THEN
        RAISE EXCEPTION 'approval.not_current_step';
    END IF;
    IF v.created_by = actor THEN
        RAISE EXCEPTION 'approval.author_forbidden';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_allowed(d.id) OR NOT app.has_permission('documents', 'approve') THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    IF app.user_role_code() = st.role_code THEN
        RETURN NULL;
    END IF;
    SELECT dl.delegator_id INTO deleg FROM public.document_delegation dl
    WHERE dl.delegate_id = actor AND dl.role_code = st.role_code AND dl.revoked_at IS NULL
      AND app.local_today() BETWEEN dl.valid_from AND dl.valid_to
      AND EXISTS (SELECT 1 FROM public."user" u JOIN public.role r ON r.id = u.role_id
                  WHERE u.id = dl.delegator_id AND u.is_active AND u.deleted_at IS NULL
                    AND r.code = dl.role_code)
    ORDER BY dl.valid_from, dl.id
    LIMIT 1;
    IF deleg IS NULL THEN
        RAISE EXCEPTION 'approval.not_your_step';
    END IF;
    IF deleg = v.created_by THEN
        RAISE EXCEPTION 'approval.author_forbidden';
    END IF;
    RETURN deleg;
END $$
"""

ROUTE_CHECK_PREVIOUS = ROUTE_CHECK.replace(
    "    ORDER BY dl.valid_from, dl.id\n    LIMIT 1;\n", "    LIMIT 1;\n"
)

PERSON_NAMES = """
CREATE FUNCTION app.document_person_names(p_documents uuid[])
RETURNS TABLE (o_user_id uuid, o_full_name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH docs AS (
        SELECT d.id FROM public.document d
        WHERE d.id = ANY (p_documents) AND d.deleted_at IS NULL AND app.is_internal()
          AND app.document_allowed(d.id)
    ), people AS (
        SELECT v.created_by AS uid FROM public.document_version v JOIN docs ON docs.id = v.document_id
        UNION SELECT x.actor_id FROM public.approval_decision x JOIN docs ON docs.id = x.document_id
        UNION SELECT x.on_behalf_of FROM public.approval_decision x JOIN docs ON docs.id = x.document_id
        UNION SELECT s.decided_by FROM public.version_route_step s JOIN docs ON docs.id = s.document_id
        UNION SELECT s.on_behalf_of FROM public.version_route_step s JOIN docs ON docs.id = s.document_id
        UNION SELECT e.actor_id FROM public.document_status_event e
            JOIN public.document_version v ON v.id = e.document_version_id
            JOIN docs ON docs.id = v.document_id
    )
    SELECT u.id, u.full_name FROM public."user" u
    WHERE u.id IN (SELECT uid FROM people WHERE uid IS NOT NULL)
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(DELEGATION_PRECHECK)
    op.execute("RESET ROLE")
    op.drop_constraint(
        op.f("ck_document_delegation_max_period"), "document_delegation", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_delegation_max_period"),
        "document_delegation",
        sa.text("valid_to - valid_from <= 59"),
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(DELEGATION_CREATE)
    op.execute(DELEGATION_INSERT_GUARD)
    op.execute("REVOKE ALL ON FUNCTION app.delegation_insert_guard() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.delegation_insert_guard() TO kut_owner")
    op.execute(ROUTE_CHECK)
    op.execute(PERSON_NAMES)
    op.execute("REVOKE ALL ON FUNCTION app.document_person_names(uuid[]) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.document_person_names(uuid[]) TO kut_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.version_move(uuid, text, text, uuid) TO kut_worker")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION app.document_version_transition(uuid, text, text) FROM kut_worker"
    )
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER document_delegation_insert_guard BEFORE INSERT ON public.document_delegation "
        "FOR EACH ROW EXECUTE FUNCTION app.delegation_insert_guard()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS document_delegation_insert_guard ON public.document_delegation"
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.document_version_transition(uuid, text, text) TO kut_worker"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION app.version_move(uuid, text, text, uuid) FROM kut_worker"
    )
    op.execute("DROP FUNCTION IF EXISTS app.document_person_names(uuid[])")
    op.execute(ROUTE_CHECK_PREVIOUS)
    op.execute("DROP FUNCTION IF EXISTS app.delegation_insert_guard()")
    op.execute(DELEGATION_CREATE_PREVIOUS)
    op.execute("RESET ROLE")
    op.drop_constraint(
        op.f("ck_document_delegation_max_period"), "document_delegation", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_delegation_max_period"),
        "document_delegation",
        sa.text("valid_to - valid_from <= 60"),
    )
