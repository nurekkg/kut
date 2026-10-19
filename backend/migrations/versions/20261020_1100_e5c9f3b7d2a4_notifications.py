"""S6b, шаг 2: уведомления согласования через outbox (шаблоны, очередь, журнал доставки),
переключатель notify_whatsapp, события маршрута и замещения

Revision ID: e5c9f3b7d2a4
Revises: d4b8e2a6c1f3
Create Date: 2026-10-20 11:00:00

Строку outbox пишут функции app.notify_* внутри той же транзакции, что снимок маршрута, решение или
замещение: откат транзакции откатывает и строку. dedup_key уникален: повтор события не создаёт
вторую строку. Доставки пишет только kut_worker; журнал читает право notifications:read.
Текст сообщения - только вид документа, номер и ссылка; ФИО, суммы и токены в outbox не попадают.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.helpers import (
    business_table,
    create_audit_triggers,
    drop_audit_triggers,
    force_rls,
)

revision: str = "e5c9f3b7d2a4"
down_revision: str | Sequence[str] | None = "d4b8e2a6c1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = ("approval_requested", "approval_approved", "approval_rejected", "delegation_assigned")
EVENT_LIST = ", ".join(f"'{event}'" for event in EVENTS)

# Шаблоны WhatsApp: текст совпадает с wiki/ops/whatsapp-connect.md. approval = pending до одобрения Meta.
TEMPLATES = (
    (
        "approval_requested",
        "kut_approval_requested",
        "Документ «{{1}}» № {{2}} ждёт вашего решения в системе. Открыть: {{3}}",
        "kind,number,link",
    ),
    (
        "approval_approved",
        "kut_approval_approved",
        "Документ «{{1}}» № {{2}} согласован. Открыть: {{3}}",
        "kind,number,link",
    ),
    (
        "approval_rejected",
        "kut_approval_rejected",
        "Документ «{{1}}» № {{2}} возвращён на доработку. Причину можно посмотреть в системе: {{3}}",
        "kind,number,link",
    ),
    (
        "delegation_assigned",
        "kut_delegation_assigned",
        "Вам назначено замещение по согласованию документов до {{1}}. Открыть раздел: {{2}}",
        "until,link",
    ),
)

PERSON_OK = """
CREATE FUNCTION app.notify_person_ok(p_user uuid, p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public."user" u JOIN public.role r ON r.id = u.role_id
        WHERE u.id = p_user AND u.is_active AND u.deleted_at IS NULL AND u.notify_whatsapp
          AND u.phone IS NOT NULL AND r.scope <> 'counterparty'
          AND (p_document IS NULL OR r.scope = 'group' OR EXISTS (
                SELECT 1 FROM public.document d
                WHERE d.id = p_document AND d.deleted_at IS NULL AND (
                    (d.project_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.user_project_access a
                        WHERE a.user_id = u.id AND a.project_id = d.project_id
                          AND a.deleted_at IS NULL))
                    OR (d.project_id IS NULL AND EXISTS (
                        SELECT 1 FROM public.user_legal_entity_access l
                        WHERE l.user_id = u.id AND l.legal_entity_id = d.legal_entity_id
                          AND l.deleted_at IS NULL))))))
$$
"""

NOTIFY_USER = """
CREATE FUNCTION app.notify_user(p_event text, p_dedup text, p_recipient uuid, p_document uuid,
    p_template text, p_params jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT app.notify_person_ok(p_recipient, p_document) THEN
        RETURN;
    END IF;
    INSERT INTO public.notification_outbox
        (event_type, dedup_key, recipient_user_id, document_id, template_code, params)
    VALUES (p_event, p_dedup, p_recipient, p_document, p_template, p_params)
    ON CONFLICT (dedup_key) DO NOTHING;
END $$
"""

NOTIFY_CURRENT = """
CREATE FUNCTION app.notify_current_step(p_version uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    cur_attempt integer;
    cur_pos integer;
    cur_role text;
    r record;
BEGIN
    SELECT s.attempt, s.position, s.role_code INTO cur_attempt, cur_pos, cur_role
    FROM public.version_route_step s
    WHERE s.version_id = p_version AND s.status = 'pending'
      AND s.attempt = (SELECT max(x.attempt) FROM public.version_route_step x
                       WHERE x.version_id = p_version)
    ORDER BY s.position
    LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    FOR r IN
        SELECT u.id AS uid FROM public."user" u JOIN public.role ro ON ro.id = u.role_id
        WHERE ro.code = cur_role
        UNION
        SELECT dl.delegate_id FROM public.document_delegation dl
        WHERE dl.role_code = cur_role AND dl.revoked_at IS NULL AND dl.deleted_at IS NULL
          AND dl.delegator_id IS DISTINCT FROM v.created_by
          AND app.local_today() BETWEEN dl.valid_from AND dl.valid_to
    LOOP
        IF r.uid IS DISTINCT FROM v.created_by THEN
            PERFORM app.notify_user('approval_requested',
                'req:' || p_version || ':' || cur_attempt || ':' || cur_pos || ':' || r.uid,
                r.uid, d.id, 'approval_requested',
                jsonb_build_object('kind', d.kind, 'number', coalesce(d.registration_no, ''),
                                   'document_id', d.id));
        END IF;
    END LOOP;
END $$
"""

NOTIFY_OUTCOME = """
CREATE FUNCTION app.notify_outcome(p_version uuid, p_attempt integer, p_event text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
    sender uuid;
    r record;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    SELECT e.actor_id INTO sender FROM public.document_status_event e
    WHERE e.document_version_id = p_version AND e.to_status = 'on_approval'
    ORDER BY e.created_at DESC, e.id DESC
    LIMIT 1;
    FOR r IN
        SELECT DISTINCT x.uid FROM (VALUES (v.created_by), (sender)) AS x(uid)
        WHERE x.uid IS NOT NULL
    LOOP
        PERFORM app.notify_user(p_event,
            CASE WHEN p_event = 'approval_rejected'
                 THEN 'rej:' || p_version || ':' || p_attempt || ':' || r.uid
                 ELSE 'ok:' || p_version || ':' || r.uid END,
            r.uid, d.id, p_event,
            jsonb_build_object('kind', d.kind, 'number', coalesce(d.registration_no, ''),
                               'document_id', d.id));
    END LOOP;
END $$
"""

NOTIFY_DELEGATION = """
CREATE FUNCTION app.notify_delegation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- Уведомление получает только тот, кто может согласовать (documents:approve), и не чаще одного раза
    -- в день: замещение нельзя использовать для рассылки сообщений сотрудникам без права согласования.
    IF EXISTS (
        SELECT 1 FROM public."user" u
        JOIN public.role_permission rp ON rp.role_id = u.role_id
        JOIN public.permission p ON p.id = rp.permission_id
        WHERE u.id = NEW.delegate_id AND p.module = 'documents' AND p.action = 'approve'
          AND p.qualifier IS NULL AND rp.deleted_at IS NULL AND p.deleted_at IS NULL
    ) THEN
        PERFORM app.notify_user('delegation_assigned',
            'deleg:' || NEW.delegate_id || ':' || app.local_today(),
            NEW.delegate_id, NULL, 'delegation_assigned',
            jsonb_build_object('until', NEW.valid_to::text));
    END IF;
    RETURN NULL;
END $$
"""

# Одобрение шаблона провайдером: оператор вызывает под kut_owner (EXECUTE выдан только владельцу).
APPROVE_TEMPLATE = """
CREATE FUNCTION app.notification_template_approve(p_code text, p_approved boolean DEFAULT true)
RETURNS integer LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH changed AS (
        UPDATE public.notification_template
        SET approval = CASE WHEN p_approved THEN 'approved' ELSE 'pending' END, updated_at = now()
        WHERE code = p_code
        RETURNING id
    )
    SELECT count(*)::integer FROM changed
$$
"""

APPEND_ONLY = """
CREATE FUNCTION app.notification_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'notification.append_only';
END $$
"""

ROUTE_SEND = """
CREATE OR REPLACE FUNCTION app.route_send(p_version uuid) RETURNS integer
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
    PERFORM pg_advisory_xact_lock(hashtextextended(p_version::text, 0));
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
    PERFORM app.notify_current_step(p_version);
    RETURN attempt;
END $$
"""

ROUTE_SEND_PREVIOUS = ROUTE_SEND.replace("    PERFORM app.notify_current_step(p_version);\n", "")

DECISION_AFTER = """
CREATE OR REPLACE FUNCTION app.approval_decision_after() RETURNS trigger
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
        PERFORM app.notify_outcome(NEW.version_id, NEW.attempt, 'approval_rejected');
    ELSIF NOT EXISTS (SELECT 1 FROM public.version_route_step
                      WHERE version_id = NEW.version_id AND attempt = NEW.attempt
                        AND status = 'pending') THEN
        PERFORM app.version_move(NEW.version_id, 'approved', NULL, NEW.actor_id);
        PERFORM app.notify_outcome(NEW.version_id, NEW.attempt, 'approval_approved');
    ELSE
        PERFORM app.notify_current_step(NEW.version_id);
    END IF;
    RETURN NULL;
END $$
"""

DECISION_AFTER_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.approval_decision_after() RETURNS trigger
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
"""

FUNCTIONS = (
    PERSON_OK,
    NOTIFY_USER,
    NOTIFY_CURRENT,
    NOTIFY_OUTCOME,
    NOTIFY_DELEGATION,
    APPEND_ONLY,
    APPROVE_TEMPLATE,
)
TRIGGER_FUNCTIONS = ("notify_delegation()", "notification_append_only()")


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("notify_whatsapp", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.execute('GRANT SELECT (notify_whatsapp), UPDATE (notify_whatsapp) ON "user" TO kut_app')
    op.execute('GRANT SELECT (notify_whatsapp) ON "user" TO kut_worker')

    business_table(
        "notification_template",
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("wa_name", sa.String(120), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("approval", sa.String(20), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("params", sa.String(200), nullable=False),
        sa.UniqueConstraint("code", name=op.f("uq_notification_template_code")),
        sa.CheckConstraint(
            "channel IN ('whatsapp')", name=op.f("ck_notification_template_channel")
        ),
        sa.CheckConstraint(
            "approval IN ('approved', 'pending')", name=op.f("ck_notification_template_approval")
        ),
    )
    values = ", ".join(
        f"('{code}', 'whatsapp', '{wa_name}', 'ru', 'pending', '{preview}', '{params}')"
        for code, wa_name, preview, params in TEMPLATES
    )
    op.execute(
        "INSERT INTO notification_template (code, channel, wa_name, language, approval, preview, params)"
        f" VALUES {values}"
    )

    force_rls("notification_template")

    business_table(
        "notification_outbox",
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("template_code", sa.String(60), nullable=False),
        sa.Column("channel", sa.String(20), server_default=sa.text("'whatsapp'"), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(60), nullable=True),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["user.id"],
            name=op.f("fk_notification_outbox_recipient_user_id_user"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_notification_outbox_document_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["template_code"],
            ["notification_template.code"],
            name=op.f("fk_notification_outbox_template_code_notification_template"),
        ),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_notification_outbox_dedup_key")),
        sa.CheckConstraint(
            f"event_type IN ({EVENT_LIST})", name=op.f("ck_notification_outbox_event_type")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'dead')",
            name=op.f("ck_notification_outbox_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_notification_outbox_attempts")),
    )
    op.create_index(
        op.f("ix_notification_outbox_due"),
        "notification_outbox",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    force_rls("notification_outbox")

    business_table(
        "notification_delivery",
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_status", sa.Integer(), nullable=True),
        sa.Column("provider_message_id", sa.String(120), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("phone_masked", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["notification_outbox.id"],
            name=op.f("fk_notification_delivery_outbox_id_notification_outbox"),
        ),
        sa.CheckConstraint(
            "status IN ('logged', 'sent', 'retry', 'failed', 'dead')",
            name=op.f("ck_notification_delivery_status"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_notification_delivery_attempt")),
    )
    force_rls("notification_delivery")

    for table in ("notification_template", "notification_outbox", "notification_delivery"):
        create_audit_triggers(table, frozen_columns=())

    op.execute("GRANT SELECT ON notification_template TO kut_app, kut_worker")
    op.execute("GRANT SELECT ON notification_outbox TO kut_app, kut_worker")
    op.execute(
        "GRANT UPDATE (status, attempts, next_attempt_at, last_error_code, updated_at) "
        "ON notification_outbox TO kut_worker"
    )
    op.execute("GRANT SELECT, INSERT ON notification_outbox TO kut_auth")
    op.execute("GRANT UPDATE (approval, updated_at) ON notification_template TO kut_auth")
    op.execute("GRANT SELECT ON notification_delivery TO kut_app")
    op.execute("GRANT SELECT, INSERT ON notification_delivery TO kut_worker")

    op.execute(
        "CREATE POLICY notification_template_select_app ON notification_template "
        "FOR SELECT TO kut_app USING (true)"
    )
    op.execute(
        "CREATE POLICY notification_outbox_select_app ON notification_outbox FOR SELECT TO kut_app "
        "USING (recipient_user_id = app.user_id() OR app.has_permission('notifications', 'read'))"
    )
    op.execute(
        "CREATE POLICY notification_delivery_select_app ON notification_delivery "
        "FOR SELECT TO kut_app USING (app.has_permission('notifications', 'read'))"
    )

    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS:
        op.execute(sql)
    op.execute(ROUTE_SEND)
    op.execute(DECISION_AFTER)
    for name in ("notify_person_ok", "notify_user", "notify_current_step", "notify_outcome"):
        op.execute(f"REVOKE ALL ON FUNCTION app.{name} FROM PUBLIC")
    for name in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION app.{name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{name} TO kut_owner")
    op.execute(
        "REVOKE ALL ON FUNCTION app.notification_template_approve(text, boolean) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.notification_template_approve(text, boolean) TO kut_owner"
    )
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER document_delegation_notify AFTER INSERT ON public.document_delegation "
        "FOR EACH ROW EXECUTE FUNCTION app.notify_delegation()"
    )
    op.execute(
        "CREATE TRIGGER notification_delivery_append_only BEFORE UPDATE OR DELETE "
        "ON public.notification_delivery FOR EACH ROW EXECUTE FUNCTION app.notification_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS notification_delivery_append_only ON notification_delivery")
    op.execute("DROP TRIGGER IF EXISTS document_delegation_notify ON document_delegation")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(ROUTE_SEND_PREVIOUS)
    op.execute(DECISION_AFTER_PREVIOUS)
    op.execute("DROP FUNCTION IF EXISTS app.notification_template_approve(text, boolean)")
    for name in (
        "notification_append_only",
        "notify_delegation",
        "notify_outcome",
        "notify_current_step",
        "notify_user",
        "notify_person_ok",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS app.{name}")
    op.execute("RESET ROLE")
    for table in ("notification_delivery", "notification_outbox", "notification_template"):
        drop_audit_triggers(table)
    op.drop_table("notification_delivery")
    op.drop_table("notification_outbox")
    op.drop_table("notification_template")
    op.drop_column("user", "notify_whatsapp")
