"""S6b-close: WhatsApp только по согласию (opt-in), статусы disabled и deferred, владелец выключает

Revision ID: f2a6c8d1b4e7
Revises: e5c9f3b7d2a4
Create Date: 2026-10-20 12:00:00

WhatsApp по умолчанию выключен у всех (в том числе у существующих строк). Включает только сам
пользователь, выключает сам или владелец (app.admin_whatsapp_off). Даты согласия и отзыва ставит гард
user_notify_guard, а не приложение. Системный вход (сессия kut_owner или kut_worker без app.user_id())
меняет флаг миграциями и сидом. Строка outbox не создаётся для удалённого документа. Статусы outbox -
disabled (production без отправки), delivery - deferred (с defer_until) и disabled. kut_worker больше не
выполняет app.version_move: сид переходит на kut_auth от владельца.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6c8d1b4e7"
down_revision: str | Sequence[str] | None = "e5c9f3b7d2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_NOTIFY_GUARD = """
CREATE OR REPLACE FUNCTION app.user_notify_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    actor uuid := app.user_id();
BEGIN
    IF NEW.notify_whatsapp IS NOT DISTINCT FROM OLD.notify_whatsapp THEN
        RETURN NEW;
    END IF;
    IF actor IS NULL THEN
        IF session_user NOT IN ('kut_owner', 'kut_worker') THEN
            RAISE EXCEPTION 'user.notify_forbidden';
        END IF;
    ELSIF NEW.notify_whatsapp THEN
        IF NEW.id IS DISTINCT FROM actor THEN
            RAISE EXCEPTION 'user.notify_forbidden';
        END IF;
    ELSIF NEW.id IS DISTINCT FROM actor AND app.user_role_code() IS DISTINCT FROM 'owner' THEN
        RAISE EXCEPTION 'user.notify_forbidden';
    END IF;
    IF NEW.notify_whatsapp THEN
        NEW.notify_whatsapp_consent_at := now();
    ELSE
        NEW.notify_whatsapp_revoked_at := now();
    END IF;
    RETURN NEW;
END $$
"""

USER_NOTIFY_GUARD_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.user_notify_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.notify_whatsapp IS DISTINCT FROM OLD.notify_whatsapp
       AND app.user_id() IS NOT NULL AND NEW.id IS DISTINCT FROM app.user_id() THEN
        RAISE EXCEPTION 'user.notify_forbidden';
    END IF;
    RETURN NEW;
END $$
"""

ADMIN_WHATSAPP_OFF = """
CREATE FUNCTION app.admin_whatsapp_off(p_user uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF app.user_role_code() IS DISTINCT FROM 'owner' THEN
        RAISE EXCEPTION 'user.notify_forbidden';
    END IF;
    UPDATE public."user" SET notify_whatsapp = false, updated_at = now()
    WHERE id = p_user AND deleted_at IS NULL AND notify_whatsapp;
    RETURN EXISTS (SELECT 1 FROM public."user" u WHERE u.id = p_user AND u.deleted_at IS NULL);
END $$
"""

NOTIFY_USER = """
CREATE OR REPLACE FUNCTION app.notify_user(p_event text, p_dedup text, p_recipient uuid,
    p_document uuid, p_template text, p_params jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.document d WHERE d.id = p_document AND d.deleted_at IS NOT NULL) THEN
        RETURN;
    END IF;
    IF NOT app.notify_person_ok(p_recipient, p_document) THEN
        RETURN;
    END IF;
    INSERT INTO public.notification_outbox
        (event_type, dedup_key, recipient_user_id, document_id, template_code, params)
    VALUES (p_event, p_dedup, p_recipient, p_document, p_template, p_params)
    ON CONFLICT (dedup_key) DO NOTHING;
END $$
"""

NOTIFY_USER_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.notify_user(p_event text, p_dedup text, p_recipient uuid,
    p_document uuid, p_template text, p_params jsonb) RETURNS void
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


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("notify_whatsapp_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("notify_whatsapp_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("user", "notify_whatsapp", server_default=sa.text("false"))
    # Существующие строки - выключено. Владелец таблицы под FORCE RLS строк не видит, поэтому правка
    # идёт через kut_auth (BYPASSRLS). Право kut_auth на колонки нужно функции admin_whatsapp_off.
    op.execute('GRANT UPDATE (notify_whatsapp, updated_at) ON "user" TO kut_auth')
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute('UPDATE public."user" SET notify_whatsapp = false WHERE notify_whatsapp')
    op.execute(ADMIN_WHATSAPP_OFF)
    op.execute("REVOKE ALL ON FUNCTION app.admin_whatsapp_off(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.admin_whatsapp_off(uuid) TO kut_app")
    op.execute(NOTIFY_USER)
    # Гард создан владельцем kut_auth (миграция e5c9): заменять его можно только от этой роли.
    op.execute(USER_NOTIFY_GUARD)
    op.execute("RESET ROLE")
    op.execute(
        'GRANT SELECT (notify_whatsapp_consent_at, notify_whatsapp_revoked_at) ON "user" '
        "TO kut_app, kut_worker"
    )

    op.drop_constraint(op.f("ck_notification_outbox_status"), "notification_outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_notification_outbox_status"),
        "notification_outbox",
        sa.text("status IN ('queued', 'sent', 'failed', 'dead', 'disabled')"),
    )
    op.drop_constraint(
        op.f("ck_notification_delivery_status"), "notification_delivery", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_delivery_status"),
        "notification_delivery",
        sa.text("status IN ('logged', 'sent', 'retry', 'failed', 'dead', 'deferred', 'disabled')"),
    )
    op.add_column(
        "notification_delivery",
        sa.Column("defer_until", sa.DateTime(timezone=True), nullable=True),
    )
    # Функция принадлежит kut_auth: права на неё меняет только владелец.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION app.version_move(uuid, text, text, uuid) FROM kut_worker"
    )
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("GRANT EXECUTE ON FUNCTION app.version_move(uuid, text, text, uuid) TO kut_worker")
    op.execute("RESET ROLE")
    op.drop_column("notification_delivery", "defer_until")
    op.drop_constraint(
        op.f("ck_notification_delivery_status"), "notification_delivery", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_delivery_status"),
        "notification_delivery",
        sa.text("status IN ('logged', 'sent', 'retry', 'failed', 'dead')"),
    )
    op.drop_constraint(op.f("ck_notification_outbox_status"), "notification_outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_notification_outbox_status"),
        "notification_outbox",
        sa.text("status IN ('queued', 'sent', 'failed', 'dead')"),
    )
    # Функции принадлежат kut_auth: их и права на них меняет только владелец.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(NOTIFY_USER_PREVIOUS)
    op.execute(USER_NOTIFY_GUARD_PREVIOUS)
    op.execute("REVOKE EXECUTE ON FUNCTION app.admin_whatsapp_off(uuid) FROM kut_app")
    op.execute("DROP FUNCTION IF EXISTS app.admin_whatsapp_off(uuid)")
    op.execute("RESET ROLE")
    op.execute('REVOKE UPDATE (notify_whatsapp, updated_at) ON "user" FROM kut_auth')
    op.execute(
        'REVOKE SELECT (notify_whatsapp_consent_at, notify_whatsapp_revoked_at) ON "user" '
        "FROM kut_app, kut_worker"
    )
    op.drop_column("user", "notify_whatsapp_revoked_at")
    op.drop_column("user", "notify_whatsapp_consent_at")
    op.alter_column("user", "notify_whatsapp", server_default=sa.text("true"))
