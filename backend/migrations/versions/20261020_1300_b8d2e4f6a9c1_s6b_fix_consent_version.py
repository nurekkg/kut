"""S6b-fix: версия текста согласия, телефон и профиль меняет только сам пользователь

Revision ID: b8d2e4f6a9c1
Revises: f2a6c8d1b4e7
Create Date: 2026-10-20 13:00:00

Версия текста согласия (notify_whatsapp_consent_version) записывается вместе с включением WhatsApp: гард
требует её, если пользователь включает себе. Политика user_update_app (S1a): внутренний пользователь меняет
строку "user" только свою, владелец - любую.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2e4f6a9c1"
down_revision: str | Sequence[str] | None = "f2a6c8d1b4e7"
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
        IF NEW.notify_whatsapp_consent_version IS NULL THEN
            RAISE EXCEPTION 'user.consent_version_required';
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


def upgrade() -> None:
    op.add_column("user", sa.Column("notify_whatsapp_consent_version", sa.Text(), nullable=True))
    op.execute('GRANT UPDATE (notify_whatsapp_consent_version) ON "user" TO kut_app')
    op.execute('GRANT SELECT (notify_whatsapp_consent_version) ON "user" TO kut_app, kut_worker')
    # Функции принадлежат kut_auth: заменяем их от владельца функций.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(USER_NOTIFY_GUARD)
    op.execute("RESET ROLE")
    op.execute('DROP POLICY user_update_app ON public."user"')
    op.execute(
        'CREATE POLICY user_update_app ON public."user" FOR UPDATE TO kut_app '
        "USING (id = (SELECT app.user_id()) OR (SELECT app.user_role_code()) = 'owner') "
        "WITH CHECK (id = (SELECT app.user_id()) OR (SELECT app.user_role_code()) = 'owner')"
    )


def downgrade() -> None:
    op.execute('DROP POLICY user_update_app ON public."user"')
    op.execute(
        'CREATE POLICY user_update_app ON public."user" FOR UPDATE TO kut_app '
        "USING ((SELECT app.is_internal())) WITH CHECK ((SELECT app.is_internal()))"
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(USER_NOTIFY_GUARD_PREVIOUS)
    op.execute("RESET ROLE")
    op.drop_column("user", "notify_whatsapp_consent_version")
