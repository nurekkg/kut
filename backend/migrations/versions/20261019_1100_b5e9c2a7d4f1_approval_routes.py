"""маршруты согласования версий: шаблоны, снимок, решения, замещение, подписанный скан (S6a-2)

Revision ID: b5e9c2a7d4f1
Revises: a8d3f1c6e4b2
Create Date: 2026-10-19 11:00:00.000000

Модель - ADR-015 часть 5 (решения 2026-10-19). Шаблон по группе видов (денежные - `money`, прочие -
`other`), снимок шагов на попытку (`version_route_step`), журнал решений (`approval_decision`, APPEND_ONLY),
замещение (`document_delegation`). Переходы версий делает внутренняя `app.version_move` (без проверки
пользовательской сессии: её вызывают только функции маршрута); пользовательская сессия к ней не имеет доступа.
Решение пишет функция `app.route_decide` (владелец kut_auth): гард проверяет то же повторно. Причина
решения видна по `app.decision_reason`: финансовые виды - держателям права на деньги, автору и участникам
маршрута; нефинансовые - тем, кто видит документ. Подписанный скан - версия `signed` от согласованной.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, drop_audit_triggers, force_rls

revision: str = "b5e9c2a7d4f1"
down_revision: str | Sequence[str] | None = "a8d3f1c6e4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CODES = ("foreman", "estimator", "technical_director", "finance_director", "owner")
ROLE_LIST = ", ".join(f"'{code}'" for code in ROLE_CODES)

DECISION_COLUMNS = (
    "id, created_at, updated_at, document_id, version_id, legal_entity_id, project_id, attempt,"
    " position, role_code, decision, actor_id, on_behalf_of, decided_at"
)

FUNCTIONS: tuple[str, ...] = (
    # Внутренний переход версии: тот же граф, что у document_version_transition, без проверки сессии;
    # актор пишется в журнал событий.
    """
CREATE FUNCTION app.version_move(p_version uuid, p_to text, p_comment text, p_actor uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    r public.document_version;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF NOT (
        (v.status = 'draft' AND p_to = 'on_approval')
        OR (v.status = 'on_approval' AND p_to IN ('approved', 'draft'))
        OR (v.status = 'approved' AND p_to IN ('signed', 'archived'))) THEN
        RAISE EXCEPTION 'document.transition_invalid';
    END IF;
    IF p_to = 'draft' AND (p_comment IS NULL OR btrim(p_comment) = '') THEN
        RAISE EXCEPTION 'document.comment_required';
    END IF;
    UPDATE public.document_version SET status = p_to, updated_at = now() WHERE id = p_version;
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (p_version, v.project_id, v.legal_entity_id, v.status, p_to,
            NULLIF(btrim(p_comment), ''), p_actor);
    IF p_to = 'approved' THEN
        FOR r IN SELECT * FROM public.document_version
                 WHERE document_id = v.document_id AND status IN ('approved', 'signed')
                   AND id <> p_version LOOP
            UPDATE public.document_version SET status = 'archived', updated_at = now()
            WHERE id = r.id;
            INSERT INTO public.document_status_event
                (document_version_id, project_id, legal_entity_id, from_status, to_status,
                 comment, actor_id)
            VALUES (r.id, r.project_id, r.legal_entity_id, r.status, 'archived', NULL, p_actor);
        END LOOP;
        UPDATE public.document SET current_version_id = p_version, updated_at = now()
        WHERE id = v.document_id;
    END IF;
END $$
""",
    # Текущий шаг: наименьшая позиция в статусе pending последней попытки.
    """
CREATE FUNCTION app.route_current_position(p_version uuid) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT min(s.position) FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.status = 'pending'
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version)
$$
""",
    # Проверки решения. Возвращает замещаемого (NULL, если решает сам держатель роли шага).
    """
CREATE FUNCTION app.route_check(p_version uuid, p_position integer, p_decision text, p_reason text)
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
    IF NOT app.document_allowed(d.id) OR NOT app.has_permission('documents', 'approve') THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    IF app.user_role_code() = st.role_code THEN
        RETURN NULL;
    END IF;
    SELECT dl.delegator_id INTO deleg FROM public.document_delegation dl
    WHERE dl.delegate_id = actor AND dl.role_code = st.role_code AND dl.revoked_at IS NULL
      AND app.local_today() BETWEEN dl.valid_from AND dl.valid_to
    LIMIT 1;
    IF deleg IS NULL THEN
        RAISE EXCEPTION 'approval.not_your_step';
    END IF;
    RETURN deleg;
END $$
""",
    # Решение. Повтор того же решения по тому же шагу тем же актором - no-op: возвращается запись.
    """
CREATE FUNCTION app.route_decide(p_version uuid, p_position integer, p_decision text, p_reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := app.user_id();
    prev public.approval_decision;
    onb uuid;
    newid uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    SELECT * INTO prev FROM public.approval_decision x
    WHERE x.version_id = p_version AND x.position = p_position
      AND x.attempt = (SELECT max(y.attempt) FROM public.version_route_step y
                       WHERE y.version_id = p_version);
    IF FOUND THEN
        IF prev.actor_id = actor AND prev.decision = p_decision THEN
            RETURN prev.id;
        END IF;
        RAISE EXCEPTION 'approval.not_current_step';
    END IF;
    onb := app.route_check(p_version, p_position, p_decision, p_reason);
    INSERT INTO public.approval_decision (document_id, version_id, legal_entity_id, project_id,
        attempt, position, role_code, decision, reason, actor_id, on_behalf_of, decided_at)
    SELECT v.document_id, v.id, v.legal_entity_id, v.project_id, s.attempt, s.position, s.role_code,
        p_decision, CASE WHEN p_decision = 'reject' THEN btrim(p_reason) END, actor, onb, now()
    FROM public.version_route_step s
    JOIN public.document_version v ON v.id = s.version_id
    WHERE s.version_id = p_version AND s.position = p_position
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version)
    RETURNING id INTO newid;
    RETURN newid;
END $$
""",
    # Отправка на согласование: снимок шагов шаблона группы вида, новая попытка, переход в on_approval.
    """
CREATE FUNCTION app.route_send(p_version uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    tmpl uuid;
    attempt integer;
BEGIN
    IF app.user_id() IS NULL THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF v.status <> 'draft' THEN
        RAISE EXCEPTION 'approval.version_not_draft';
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF d.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'document.archived';
    END IF;
    IF NOT app.document_writable(d.id) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    SELECT t.id INTO tmpl FROM public.approval_route_template t
    WHERE t.code = CASE WHEN d.financial THEN 'money' ELSE 'other' END AND t.deleted_at IS NULL;
    attempt := coalesce((SELECT max(s.attempt) FROM public.version_route_step s
                         WHERE s.version_id = p_version), 0) + 1;
    INSERT INTO public.version_route_step (document_id, version_id, legal_entity_id, project_id,
        attempt, position, role_code, status)
    SELECT d.id, v.id, v.legal_entity_id, v.project_id, attempt, st.position, st.role_code, 'pending'
    FROM public.approval_route_step_template st
    WHERE st.template_id = tmpl AND st.deleted_at IS NULL
    ORDER BY st.position;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval.template_empty';
    END IF;
    PERFORM app.version_move(p_version, 'on_approval', NULL, app.user_id());
    RETURN attempt;
END $$
""",
    # Может ли текущий пользователь решить текущий шаг (для интерфейса; без исключений).
    """
CREATE FUNCTION app.route_can_decide(p_version uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    pos integer;
    role text;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR v.status <> 'on_approval' OR app.user_id() IS NULL THEN
        RETURN false;
    END IF;
    pos := app.route_current_position(p_version);
    SELECT s.role_code INTO role FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.position = pos
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version);
    RETURN pos IS NOT NULL AND v.created_by IS DISTINCT FROM app.user_id()
        AND app.has_permission('documents', 'approve')
        AND app.document_allowed(v.document_id)
        AND (app.user_role_code() = role OR EXISTS (
            SELECT 1 FROM public.document_delegation dl
            WHERE dl.delegate_id = app.user_id() AND dl.role_code = role AND dl.revoked_at IS NULL
              AND app.local_today() BETWEEN dl.valid_from AND dl.valid_to));
END $$
""",
    # Участник маршрута версии: актор или замещаемый хотя бы одного решения, либо замещающий по делегации шагов.
    """
CREATE FUNCTION app.route_participant(p_version uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    RETURN EXISTS (SELECT 1 FROM public.approval_decision d
                   WHERE d.version_id = p_version
                     AND (d.actor_id = app.user_id() OR d.on_behalf_of = app.user_id()))
        OR EXISTS (SELECT 1 FROM public.version_route_step s
                   JOIN public.document_delegation dl
                     ON dl.role_code = s.role_code AND dl.delegate_id = app.user_id()
                    AND dl.revoked_at IS NULL
                   WHERE s.version_id = p_version);
END $$
""",
    # Причина решения: финансовый документ - держатели права на деньги, автор, участники маршрута;
    # нефинансовый - кто видит документ.
    """
CREATE FUNCTION app.decision_reason(p_decision uuid) RETURNS text
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
""",
    # Участники комментариев событий и версии: прежнее правило плюс участие в маршруте.
    """
CREATE OR REPLACE FUNCTION app.event_comment(p_event uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN app.document_comment_visible(v.document_id) OR app.route_participant(v.id)
                THEN e.comment END
    FROM public.document_status_event e
    JOIN public.document_version v ON v.id = e.document_version_id
    WHERE e.id = p_event
$$
""",
    """
CREATE OR REPLACE FUNCTION app.version_comment(p_version uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN app.document_comment_visible(v.document_id) OR app.route_participant(v.id)
                THEN v.comment END
    FROM public.document_version v WHERE v.id = p_version
$$
""",
    # Шаблон маршрута: только владелец и техдиректор (право documents:write:routes).
    f"""
CREATE FUNCTION app.route_template_save(p_code text, p_roles text[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    tmpl uuid;
    r text;
BEGIN
    IF NOT app.has_permission('documents', 'write', 'routes') THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    SELECT t.id INTO tmpl FROM public.approval_route_template t
    WHERE t.code = p_code AND t.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval.template_not_found';
    END IF;
    IF p_roles IS NULL OR cardinality(p_roles) = 0 THEN
        RAISE EXCEPTION 'approval.template_empty';
    END IF;
    IF (SELECT count(DISTINCT x) FROM unnest(p_roles) x) <> cardinality(p_roles) THEN
        RAISE EXCEPTION 'approval.template_invalid';
    END IF;
    FOREACH r IN ARRAY p_roles LOOP
        IF r NOT IN ({ROLE_LIST}) THEN
            RAISE EXCEPTION 'approval.role_not_allowed';
        END IF;
    END LOOP;
    UPDATE public.approval_route_step_template
    SET deleted_at = now(), updated_at = now(), updated_by = app.user_id()
    WHERE template_id = tmpl AND deleted_at IS NULL;
    INSERT INTO public.approval_route_step_template (template_id, position, role_code)
    SELECT tmpl, t.ord::integer, t.role FROM unnest(p_roles) WITH ORDINALITY AS t(role, ord);
END $$
""",
    # Замещение: делегирует только держатель роли, себе не назначают, период не раньше сегодня.
    """
CREATE FUNCTION app.delegation_create(p_delegate uuid, p_role text, p_from date, p_to date)
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
    IF NOT EXISTS (SELECT 1 FROM public."user" u WHERE u.id = p_delegate AND u.is_active) THEN
        RAISE EXCEPTION 'approval.delegate_not_found';
    END IF;
    INSERT INTO public.document_delegation (delegator_id, delegate_id, role_code, valid_from, valid_to)
    VALUES (actor, p_delegate, p_role, p_from, p_to)
    RETURNING id INTO newid;
    RETURN newid;
END $$
""",
    """
CREATE FUNCTION app.delegation_revoke(p_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.document_delegation SET revoked_at = now(), updated_at = now()
    WHERE id = p_id AND delegator_id = app.user_id() AND revoked_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approval.delegation_not_found';
    END IF;
END $$
""",
    # Гард решения (BEFORE INSERT): те же проверки, что у route_decide, и актор - текущий пользователь.
    """
CREATE FUNCTION app.approval_decision_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.actor_id IS DISTINCT FROM app.user_id() THEN
        RAISE EXCEPTION 'approval.forbidden';
    END IF;
    IF NEW.on_behalf_of IS DISTINCT FROM app.route_check(NEW.version_id, NEW.position,
                                                       NEW.decision, NEW.reason) THEN
        RAISE EXCEPTION 'approval.not_your_step';
    END IF;
    RETURN NEW;
END $$
""",
    # После решения (AFTER INSERT): шаг закрывается; reject возвращает версию в черновик с причиной,
    # последний approve переводит в approved.
    """
CREATE FUNCTION app.approval_decision_after() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.version_route_step
    SET status = CASE WHEN NEW.decision = 'approve' THEN 'approved' ELSE 'rejected' END,
        decided_by = NEW.actor_id, on_behalf_of = NEW.on_behalf_of, decided_at = NEW.decided_at,
        updated_at = now()
    WHERE version_id = NEW.version_id AND attempt = NEW.attempt AND position = NEW.position;
    IF NEW.decision = 'reject' THEN
        UPDATE public.version_route_step SET status = 'skipped', updated_at = now()
        WHERE version_id = NEW.version_id AND attempt = NEW.attempt AND status = 'pending';
        PERFORM app.version_move(NEW.version_id, 'draft', NEW.reason, NEW.actor_id);
    ELSIF NOT EXISTS (SELECT 1 FROM public.version_route_step
                      WHERE version_id = NEW.version_id AND attempt = NEW.attempt
                        AND status = 'pending') THEN
        PERFORM app.version_move(NEW.version_id, 'approved', NULL, NEW.actor_id);
    END IF;
    RETURN NULL;
END $$
""",
    """
CREATE FUNCTION app.approval_decision_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'approval.immutable';
END $$
""",
    # Шаг снимка меняет только решение: статус pending -> решённый, поля решения; удаление запрещено.
    """
CREATE FUNCTION app.version_route_step_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'approval.immutable';
    END IF;
    IF OLD.status <> 'pending' OR NEW.status = 'pending'
       OR NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.attempt IS DISTINCT FROM OLD.attempt OR NEW.position IS DISTINCT FROM OLD.position
       OR NEW.role_code IS DISTINCT FROM OLD.role_code THEN
        RAISE EXCEPTION 'approval.immutable';
    END IF;
    RETURN NEW;
END $$
""",
    # Замещение: как при создании через функцию, и отзыв только установкой revoked_at.
    """
CREATE FUNCTION app.delegation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF app.user_id() IS NOT NULL AND NEW.delegator_id IS DISTINCT FROM app.user_id() THEN
            RAISE EXCEPTION 'approval.forbidden';
        END IF;
        IF NEW.delegator_id = NEW.delegate_id THEN
            RAISE EXCEPTION 'approval.delegation_self';
        END IF;
        IF NEW.valid_from < app.local_today() THEN
            RAISE EXCEPTION 'approval.period_in_past';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.delegator_id IS DISTINCT FROM OLD.delegator_id
       OR NEW.delegate_id IS DISTINCT FROM OLD.delegate_id
       OR NEW.role_code IS DISTINCT FROM OLD.role_code
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
       OR OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'approval.immutable';
    END IF;
    RETURN NEW;
END $$
""",
    # Версия: подписанный скан - только от согласованной версии того же документа.
    """
CREATE OR REPLACE FUNCTION app.document_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
    parent_document uuid;
    parent_status text;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF NEW.status = 'signed' THEN
        IF NEW.parent_version_id IS NULL THEN
            RAISE EXCEPTION 'document.version_status_invalid';
        END IF;
        SELECT document_id, status INTO parent_document, parent_status
        FROM public.document_version WHERE id = NEW.parent_version_id;
        IF NOT FOUND OR parent_document <> NEW.document_id OR parent_status <> 'approved' THEN
            RAISE EXCEPTION 'document.parent_not_approved';
        END IF;
    ELSIF NEW.status <> 'draft' OR NEW.parent_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'document.version_status_invalid';
    END IF;
    NEW.project_id := d.project_id;
    NEW.legal_entity_id := d.legal_entity_id;
    RETURN NEW;
END $$
""",
    """
CREATE OR REPLACE FUNCTION app.document_version_created() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (NEW.id, NEW.project_id, NEW.legal_entity_id, NULL, NEW.status, NULL, app.user_id());
    RETURN NULL;
END $$
""",
    # Подписанный скан становится действующей версией; согласованная предшественница - в архив.
    """
CREATE FUNCTION app.document_version_signed_after() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM app.version_move(NEW.parent_version_id, 'archived', NULL, app.user_id());
    UPDATE public.document SET current_version_id = NEW.id, updated_at = now()
    WHERE id = NEW.document_id;
    RETURN NULL;
END $$
""",
)

FUNCTION_SIGNATURES: tuple[str, ...] = (
    "version_move(uuid, text, text, uuid)",
    "route_current_position(uuid)",
    "route_check(uuid, integer, text, text)",
    "route_decide(uuid, integer, text, text)",
    "route_send(uuid)",
    "route_can_decide(uuid)",
    "route_participant(uuid)",
    "decision_reason(uuid)",
    "route_template_save(text, text[])",
    "delegation_create(uuid, text, date, date)",
    "delegation_revoke(uuid)",
)

TRIGGER_FUNCTIONS: tuple[str, ...] = (
    "approval_decision_guard()",
    "approval_decision_after()",
    "approval_decision_immutable()",
    "version_route_step_guard()",
    "delegation_guard()",
    "document_version_signed_after()",
)

GUARD_TRIGGERS: tuple[str, ...] = (
    "CREATE TRIGGER approval_decision_guard BEFORE INSERT ON approval_decision"
    " FOR EACH ROW EXECUTE FUNCTION app.approval_decision_guard()",
    "CREATE TRIGGER approval_decision_after AFTER INSERT ON approval_decision"
    " FOR EACH ROW EXECUTE FUNCTION app.approval_decision_after()",
    "CREATE TRIGGER approval_decision_immutable BEFORE UPDATE OR DELETE ON approval_decision"
    " FOR EACH ROW EXECUTE FUNCTION app.approval_decision_immutable()",
    "CREATE TRIGGER version_route_step_guard BEFORE UPDATE OR DELETE ON version_route_step"
    " FOR EACH ROW EXECUTE FUNCTION app.version_route_step_guard()",
    "CREATE TRIGGER document_delegation_guard BEFORE INSERT OR UPDATE ON document_delegation"
    " FOR EACH ROW EXECUTE FUNCTION app.delegation_guard()",
    "CREATE TRIGGER document_version_signed_after AFTER INSERT ON document_version"
    " FOR EACH ROW WHEN (NEW.status = 'signed')"
    " EXECUTE FUNCTION app.document_version_signed_after()",
)

DENY_WRITES = "USING (false) WITH CHECK (false)"
DENY_INSERT = "WITH CHECK (false)"
DENY_INSERT = "WITH CHECK (false)"
VISIBLE_TO_READERS = "USING (app.is_internal() AND app.has_permission('documents', 'read'))"
VISIBLE_VERSION = "USING (EXISTS (SELECT 1 FROM public.document_version v WHERE v.id = version_id))"

COMMENT_OLD = (
    "CREATE OR REPLACE FUNCTION app.event_comment(p_event uuid) RETURNS text\n"
    "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$\n"
    "    SELECT CASE WHEN app.document_comment_visible(v.document_id) THEN e.comment END\n"
    "    FROM public.document_status_event e\n"
    "    JOIN public.document_version v ON v.id = e.document_version_id\n"
    "    WHERE e.id = p_event\n"
    "$$"
)
VERSION_COMMENT_OLD = (
    "CREATE OR REPLACE FUNCTION app.version_comment(p_version uuid) RETURNS text\n"
    "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$\n"
    "    SELECT CASE WHEN app.document_comment_visible(v.document_id) THEN v.comment END\n"
    "    FROM public.document_version v WHERE v.id = p_version\n"
    "$$"
)
GUARD_OLD = """
CREATE OR REPLACE FUNCTION app.document_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF NEW.status <> 'draft' OR NEW.parent_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'document.version_status_invalid';
    END IF;
    NEW.project_id := d.project_id;
    NEW.legal_entity_id := d.legal_entity_id;
    RETURN NEW;
END $$
"""
CREATED_OLD = """
CREATE OR REPLACE FUNCTION app.document_version_created() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (NEW.id, NEW.project_id, NEW.legal_entity_id, NULL, 'draft', NULL, app.user_id());
    RETURN NULL;
END $$
"""


def _table_steps() -> None:
    business_table(
        "approval_route_template",
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.CheckConstraint(
            "code IN ('money', 'other')", name=op.f("ck_approval_route_template_code")
        ),
        sa.UniqueConstraint("code", name=op.f("uq_approval_route_template_code")),
    )
    force_rls("approval_route_template")
    business_table(
        "approval_route_step_template",
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["approval_route_template.id"],
            name=op.f("fk_approval_route_step_template_template_id_approval_route_template"),
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_approval_route_step_template_position")),
        sa.CheckConstraint(
            f"role_code IN ({ROLE_LIST})", name=op.f("ck_approval_route_step_template_role_code")
        ),
    )
    op.create_index(
        op.f("uq_approval_route_step_template_template_id_position"),
        "approval_route_step_template",
        ["template_id", "position"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    force_rls("approval_route_step_template")
    business_table(
        "version_route_step",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("on_behalf_of", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_version_route_step_document_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_version_route_step_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_version_route_step_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_version_route_step_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["user.id"], name=op.f("fk_version_route_step_decided_by_user")
        ),
        sa.ForeignKeyConstraint(
            ["on_behalf_of"], ["user.id"], name=op.f("fk_version_route_step_on_behalf_of_user")
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_version_route_step_attempt")),
        sa.CheckConstraint("position >= 1", name=op.f("ck_version_route_step_position")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'skipped')",
            name=op.f("ck_version_route_step_status"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL)"
            " OR (status IN ('approved', 'rejected') AND decided_at IS NOT NULL)"
            " OR (status = 'skipped' AND decided_at IS NULL)",
            name=op.f("ck_version_route_step_decided_consistent"),
        ),
        sa.UniqueConstraint(
            "version_id",
            "attempt",
            "position",
            name=op.f("uq_version_route_step_version_attempt_pos"),
        ),
    )
    force_rls("version_route_step")
    business_table(
        "approval_decision",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("on_behalf_of", sa.Uuid(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["document.id"], name=op.f("fk_approval_decision_document_id_document")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name=op.f("fk_approval_decision_version_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_approval_decision_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entity.id"],
            name=op.f("fk_approval_decision_legal_entity_id_legal_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["user.id"], name=op.f("fk_approval_decision_actor_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["on_behalf_of"], ["user.id"], name=op.f("fk_approval_decision_on_behalf_of_user")
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')", name=op.f("ck_approval_decision_decision")
        ),
        sa.CheckConstraint(
            "decision = 'approve' OR btrim(reason) <> ''",
            name=op.f("ck_approval_decision_reason_required"),
        ),
        sa.UniqueConstraint(
            "version_id",
            "attempt",
            "position",
            name=op.f("uq_approval_decision_version_attempt_pos"),
        ),
    )
    force_rls("approval_decision")
    business_table(
        "document_delegation",
        sa.Column("delegator_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["delegator_id"], ["user.id"], name=op.f("fk_document_delegation_delegator_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["delegate_id"], ["user.id"], name=op.f("fk_document_delegation_delegate_id_user")
        ),
        sa.CheckConstraint("valid_to >= valid_from", name=op.f("ck_document_delegation_period")),
        sa.CheckConstraint(
            "delegator_id <> delegate_id", name=op.f("ck_document_delegation_not_self")
        ),
        sa.CheckConstraint(
            f"role_code IN ({ROLE_LIST})", name=op.f("ck_document_delegation_role_code")
        ),
    )
    force_rls("document_delegation")
    for table, columns in (
        ("approval_route_step_template", ("template_id",)),
        ("version_route_step", ("document_id", "version_id", "legal_entity_id")),
        ("approval_decision", ("document_id", "version_id", "legal_entity_id")),
        ("document_delegation", ("delegator_id", "delegate_id")),
    ):
        for column in columns:
            op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def upgrade() -> None:
    _table_steps()
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS:
        op.execute(sql)
    for sig in FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION app.{sig} FROM PUBLIC")
    for sig in (
        "route_current_position(uuid)",
        "user_role_code()",
        "route_send(uuid)",
        "route_decide(uuid, integer, text, text)",
        "route_can_decide(uuid)",
        "decision_reason(uuid)",
        "route_template_save(text, text[])",
        "delegation_create(uuid, text, date, date)",
        "delegation_revoke(uuid)",
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{sig} TO kut_app")
    op.execute("RESET ROLE")
    # Триггеры создаёт владелец таблиц (kut_owner): ему нужен EXECUTE на функции триггеров (как в e4a8).
    op.execute("SET LOCAL ROLE kut_auth")
    for name in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION app.{name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{name} TO kut_owner")
    op.execute("RESET ROLE")
    for sql in GUARD_TRIGGERS:
        op.execute(sql)

    # Политики: читают внутренние с правом на документы или видимые версии; записи - только функции.
    for table, read in (
        ("approval_route_template", VISIBLE_TO_READERS),
        ("approval_route_step_template", VISIBLE_TO_READERS),
        ("document_delegation", VISIBLE_TO_READERS),
        ("version_route_step", VISIBLE_VERSION),
        ("approval_decision", VISIBLE_VERSION),
    ):
        op.execute(f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app {read}")
        op.execute(
            f"CREATE POLICY {table}_insert_app ON {table} FOR INSERT TO kut_app {DENY_INSERT}"
        )
        op.execute(
            f"CREATE POLICY {table}_update_app ON {table} FOR UPDATE TO kut_app {DENY_WRITES}"
        )
    op.execute(
        "CREATE POLICY approval_decision_delete_app ON approval_decision FOR DELETE TO kut_app USING (false)"
    )

    # Права: читает приложение; пишут только функции (kut_auth). Причина решения - не колонка для приложения.
    for table in (
        "approval_route_template",
        "approval_route_step_template",
        "version_route_step",
        "document_delegation",
    ):
        op.execute(f"GRANT SELECT ON {table} TO kut_app")
    op.execute("REVOKE ALL ON approval_decision FROM kut_app")
    op.execute(f"GRANT SELECT ({DECISION_COLUMNS}) ON approval_decision TO kut_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON approval_route_template, approval_route_step_template,"
        " version_route_step, approval_decision, document_delegation TO kut_auth"
    )
    op.execute("GRANT SELECT ON document_status_event, document_version TO kut_auth")

    # Шаблоны по умолчанию (ТЗ §5.4): денежные виды - прораб, сметчик, техдиректор, финдиректор; прочие - прораб и техдиректор.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "INSERT INTO approval_route_template (code, name) VALUES"
        " ('money', 'Денежные виды: прораб, ПТО, техдиректор, финдиректор'),"
        " ('other', 'Прочие виды: прораб, техдиректор')"
    )
    op.execute(
        "INSERT INTO approval_route_step_template (template_id, position, role_code)"
        " SELECT t.id, s.position, s.role_code FROM approval_route_template t"
        " JOIN (VALUES ('money', 1, 'foreman'), ('money', 2, 'estimator'),"
        " ('money', 3, 'technical_director'), ('money', 4, 'finance_director'),"
        " ('other', 1, 'foreman'), ('other', 2, 'technical_director')) AS s(code, position, role_code)"
        " ON s.code = t.code"
    )
    op.execute("RESET ROLE")

    for table, project, legal, frozen in (
        ("approval_route_template", "", "", ("code",)),
        ("approval_route_step_template", "", "", ("template_id", "position", "role_code")),
        (
            "version_route_step",
            "project_id",
            "legal_entity_id",
            (
                "version_id",
                "document_id",
                "legal_entity_id",
                "project_id",
                "attempt",
                "position",
                "role_code",
            ),
        ),
        (
            "approval_decision",
            "project_id",
            "legal_entity_id",
            (
                "version_id",
                "document_id",
                "legal_entity_id",
                "project_id",
                "attempt",
                "position",
                "role_code",
                "decision",
                "reason",
                "actor_id",
                "on_behalf_of",
                "decided_at",
            ),
        ),
        (
            "document_delegation",
            "",
            "",
            ("delegator_id", "delegate_id", "role_code", "valid_from", "valid_to"),
        ),
    ):
        create_audit_triggers(
            table,
            project_column=project,
            legal_entity_column=legal,
            frozen_columns=frozen,
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS document_version_signed_after ON document_version")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(GUARD_OLD)
    op.execute(CREATED_OLD)
    op.execute(COMMENT_OLD)
    op.execute(VERSION_COMMENT_OLD)
    op.execute("RESET ROLE")
    for table in (
        "document_delegation",
        "approval_decision",
        "version_route_step",
        "approval_route_step_template",
        "approval_route_template",
    ):
        drop_audit_triggers(table)
    op.execute("DROP TABLE IF EXISTS document_delegation")
    op.execute("DROP TABLE IF EXISTS approval_decision")
    op.execute("DROP TABLE IF EXISTS version_route_step")
    op.execute("DROP TABLE IF EXISTS approval_route_step_template")
    op.execute("DROP TABLE IF EXISTS approval_route_template")
    op.execute("SET LOCAL ROLE kut_auth")
    for sig in (
        "delegation_revoke(uuid)",
        "delegation_create(uuid, text, date, date)",
        "route_template_save(text, text[])",
        "decision_reason(uuid)",
        "route_participant(uuid)",
        "route_can_decide(uuid)",
        "route_send(uuid)",
        "route_decide(uuid, integer, text, text)",
        "route_check(uuid, integer, text, text)",
        "route_current_position(uuid)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS app.{sig}")
    for name in (
        "approval_decision_guard()",
        "approval_decision_after()",
        "approval_decision_immutable()",
        "version_route_step_guard()",
        "delegation_guard()",
        "document_version_signed_after()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS app.{name}")
    op.execute("DROP FUNCTION IF EXISTS app.version_move(uuid, text, text, uuid)")
    op.execute("RESET ROLE")
