"""auth hardening and settings administration

Revision ID: d7c4e91a3b52
Revises: a56217376426
Create Date: 2026-10-02 09:00:00.000000

Решения PO по допущениям S1b. Ревизии S1a и S1b не менялись, всё здесь:
- refresh_rotate получает льготное окно: повтор уже использованного токена внутри окна не отзывает
  семью (приложение отдаёт ту же пару из Redis), за окном семья отзывается, как раньше;
- функции TOTP, кодов восстановления и auth_password_hash берут пользователя из контекста запроса
  (app.user_id()), а не из параметра;
- kut_worker больше не читает password_hash, totp_secret_enc и totp_key_id;
- функции администратора: создание пользователя, одноразовая ссылка активации (и сброс пароля),
  сброс 2FA, журнал событий входа; таблица user_invite, закрытая для всех логинов.
"""

import importlib.util
import pathlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import force_rls

revision: str = "d7c4e91a3b52"
down_revision: str | Sequence[str] | None = "a56217376426"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# kut_worker читает те же колонки user, что и kut_app: секреты закрыты (S1b, миграция a56217376426).
WORKER_READABLE = (
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "deleted_at",
    "phone",
    "email",
    "full_name",
    "role_id",
    "counterparty_id",
    "is_active",
    "role_ver",
    "totp_confirmed_at",
    "password_changed_at",
)

CONTEXT_USER = """
CREATE FUNCTION app.context_user() RETURNS uuid
LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'no user in the request context' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN v_user;
END $$
"""

AUTH_PASSWORD_HASH = """
CREATE FUNCTION app.auth_password_hash() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT usr.password_hash FROM public."user" usr
    WHERE usr.id = app.context_user() AND usr.deleted_at IS NULL
$$
"""

TOTP_GET = """
CREATE FUNCTION app.totp_get() RETURNS TABLE (
    o_secret_enc bytea, o_key_id text, o_confirmed boolean
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT usr.totp_secret_enc, usr.totp_key_id, usr.totp_confirmed_at IS NOT NULL
    FROM public."user" usr WHERE usr.id = app.context_user() AND usr.deleted_at IS NULL
$$
"""

TOTP_SET_PENDING = """
CREATE FUNCTION app.totp_set_pending(p_secret_enc bytea, p_key_id text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH changed AS (
        UPDATE public."user" SET totp_secret_enc = p_secret_enc, totp_key_id = p_key_id
        WHERE id = app.context_user() AND deleted_at IS NULL AND totp_confirmed_at IS NULL
        RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM changed)
$$
"""

TOTP_CONFIRM = """
CREATE FUNCTION app.totp_confirm(p_code_hashes bytea[]) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.context_user();
BEGIN
    UPDATE public."user" SET totp_confirmed_at = now()
    WHERE id = v_user AND deleted_at IS NULL
      AND totp_secret_enc IS NOT NULL AND totp_confirmed_at IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    DELETE FROM public.recovery_code WHERE user_id = v_user;
    INSERT INTO public.recovery_code (user_id, code_hash)
    SELECT v_user, code FROM unnest(p_code_hashes) AS code;
    PERFORM app.audit_event('mfa_enabled', 'user', v_user, v_user, NULL, NULL);
    RETURN true;
END $$
"""

RECOVERY_CODE_USE = """
CREATE FUNCTION app.recovery_code_use(p_hash bytea) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.context_user();
BEGIN
    UPDATE public.recovery_code SET used_at = now()
    WHERE user_id = v_user AND code_hash = p_hash AND used_at IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM app.audit_event(
        'recovery_code_used', 'user', v_user, v_user, NULL,
        jsonb_build_object('left', (SELECT count(*) FROM public.recovery_code
                                    WHERE user_id = v_user AND used_at IS NULL)));
    RETURN true;
END $$
"""

# Льготное окно: токен, использованный не раньше чем p_grace назад, повторно не отзывает семью.
# Новую пару БД не выдаёт (открытого токена в ней нет): приложение отдаёт уже выданную из Redis.
REFRESH_ROTATE = """
CREATE FUNCTION app.refresh_rotate(p_old bytea, p_new bytea, p_grace interval) RETURNS TABLE (
    o_status text, o_user_id uuid, o_family_id uuid, o_amr text[], o_expires_at timestamptz
) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_token public.refresh_token%ROWTYPE;
    v_active boolean;
    v_ttl interval;
BEGIN
    SELECT * INTO v_token FROM public.refresh_token WHERE token_hash = p_old FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid, NULL::text[], NULL::timestamptz;
        RETURN;
    END IF;
    IF v_token.revoked_at IS NOT NULL OR v_token.expires_at <= now() THEN
        RETURN QUERY SELECT 'invalid'::text, v_token.user_id, v_token.family_id, v_token.amr::text[],
                            v_token.expires_at;
        RETURN;
    END IF;
    SELECT (usr.is_active AND usr.deleted_at IS NULL) INTO v_active
    FROM public."user" usr WHERE usr.id = v_token.user_id;
    IF NOT COALESCE(v_active, false) THEN
        UPDATE public.refresh_token SET revoked_at = now(), revoked_reason = 'inactive'
        WHERE family_id = v_token.family_id AND revoked_at IS NULL;
        RETURN QUERY SELECT 'inactive'::text, v_token.user_id, v_token.family_id, v_token.amr::text[],
                            v_token.expires_at;
        RETURN;
    END IF;
    IF v_token.used_at IS NOT NULL THEN
        IF v_token.used_at > now() - p_grace THEN
            PERFORM app.audit_event('refresh_grace', 'refresh_token', v_token.family_id,
                                    v_token.user_id, NULL, NULL);
            RETURN QUERY SELECT 'grace'::text, v_token.user_id, v_token.family_id, v_token.amr::text[],
                                v_token.expires_at;
            RETURN;
        END IF;
        UPDATE public.refresh_token SET revoked_at = now(), revoked_reason = 'reuse'
        WHERE family_id = v_token.family_id AND revoked_at IS NULL;
        PERFORM app.audit_event('refresh_reuse', 'refresh_token', v_token.family_id,
                                v_token.user_id, NULL,
                                jsonb_build_object('family_id', v_token.family_id));
        RETURN QUERY SELECT 'reused'::text, v_token.user_id, v_token.family_id, v_token.amr::text[],
                            v_token.expires_at;
        RETURN;
    END IF;
    -- Срок нового токена равен сроку предъявленного: 14 или 30 дней сохраняются при ротации.
    v_ttl := v_token.expires_at - v_token.issued_at;
    UPDATE public.refresh_token SET used_at = now() WHERE id = v_token.id;
    INSERT INTO public.refresh_token (user_id, family_id, token_hash, amr, expires_at)
    VALUES (v_token.user_id, v_token.family_id, p_new, v_token.amr, now() + v_ttl);
    RETURN QUERY SELECT 'ok'::text, v_token.user_id, v_token.family_id, v_token.amr::text[], now() + v_ttl;
END $$
"""

ADMIN_CREATE_USER = """
CREATE FUNCTION app.admin_create_user(
    p_email text, p_phone text, p_full_name text, p_role_id uuid, p_counterparty_id uuid
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.require_settings_admin();
    v_scope text;
    v_id uuid;
BEGIN
    SELECT r.scope INTO v_scope FROM public.role r WHERE r.id = p_role_id AND r.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'role not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF v_scope = 'counterparty' THEN
        IF p_counterparty_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.counterparty c
            WHERE c.id = p_counterparty_id AND c.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'an external role needs an existing counterparty'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF p_counterparty_id IS NOT NULL THEN
        RAISE EXCEPTION 'an internal role cannot belong to a counterparty'
            USING ERRCODE = 'check_violation';
    END IF;
    -- Пароля нет: войти можно только после активации по одноразовой ссылке (admin_issue_invite).
    INSERT INTO public."user" (email, phone, full_name, role_id, counterparty_id, created_by, updated_by)
    VALUES (lower(NULLIF(btrim(p_email), '')), NULLIF(btrim(p_phone), ''), btrim(p_full_name),
            p_role_id, p_counterparty_id, v_actor, v_actor)
    RETURNING id INTO v_id;
    PERFORM app.audit_event('user_created', 'user', v_id, v_actor, NULL,
                            jsonb_build_object('role_id', p_role_id));
    RETURN v_id;
END $$
"""

ADMIN_ISSUE_INVITE = """
CREATE FUNCTION app.admin_issue_invite(p_user_id uuid, p_token_hash bytea, p_ttl interval)
RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.require_settings_admin();
    v_expires timestamptz := now() + p_ttl;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public."user" usr
                   WHERE usr.id = p_user_id AND usr.deleted_at IS NULL AND usr.is_active) THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    -- Новая ссылка отменяет прежние неиспользованные.
    UPDATE public.user_invite SET used_at = now() WHERE user_id = p_user_id AND used_at IS NULL;
    INSERT INTO public.user_invite (user_id, token_hash, issued_by, expires_at)
    VALUES (p_user_id, p_token_hash, v_actor, v_expires);
    PERFORM app.audit_event('invite_issued', 'user', p_user_id, v_actor, NULL,
                            jsonb_build_object('expires_at', v_expires));
    RETURN v_expires;
END $$
"""

# Без проверки администратора: доказательство права - сам токен (приложение зовёт функцию без
# пользователя в контексте). Не различает причины отказа: нет, истёк, использован, пользователь выключен.
INVITE_CONSUME = """
CREATE FUNCTION app.invite_consume(p_token_hash bytea, p_password_hash text)
RETURNS TABLE (o_user_id uuid, o_families uuid[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_invite public.user_invite%ROWTYPE;
    v_families uuid[];
BEGIN
    SELECT * INTO v_invite FROM public.user_invite
    WHERE token_hash = p_token_hash AND used_at IS NULL AND expires_at > now() FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public."user" usr
                   WHERE usr.id = v_invite.user_id AND usr.deleted_at IS NULL AND usr.is_active) THEN
        RETURN;
    END IF;
    UPDATE public.user_invite SET used_at = now() WHERE id = v_invite.id;
    UPDATE public."user" SET password_hash = p_password_hash, password_changed_at = now(),
                             updated_by = v_invite.user_id
    WHERE id = v_invite.user_id;
    v_families := app.refresh_revoke_user(v_invite.user_id, NULL, 'password_reset');
    PERFORM app.audit_event('invite_used', 'user', v_invite.user_id, v_invite.user_id, NULL,
                            jsonb_build_object('revoked_sessions', cardinality(v_families)));
    RETURN QUERY SELECT v_invite.user_id, v_families;
END $$
"""

ADMIN_RESET_TOTP = """
CREATE FUNCTION app.admin_reset_totp(p_user_id uuid)
RETURNS TABLE (o_role_ver integer, o_families uuid[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.require_settings_admin();
    v_ver integer;
    v_families uuid[];
BEGIN
    IF p_user_id = v_actor THEN
        RAISE EXCEPTION 'cannot reset own 2FA' USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE public."user" SET totp_secret_enc = NULL, totp_key_id = NULL, totp_confirmed_at = NULL,
                             role_ver = role_ver + 1, updated_by = v_actor
    WHERE id = p_user_id AND deleted_at IS NULL RETURNING role_ver INTO v_ver;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    DELETE FROM public.recovery_code WHERE user_id = p_user_id;
    v_families := app.refresh_revoke_user(p_user_id, NULL, 'mfa_reset');
    PERFORM app.audit_event('mfa_reset', 'user', p_user_id, v_actor, NULL,
                            jsonb_build_object('role_ver', v_ver));
    RETURN QUERY SELECT v_ver, v_families;
END $$
"""

ADMIN_AUTH_EVENTS = """
CREATE FUNCTION app.admin_auth_events(
    p_limit integer, p_before bigint, p_action text, p_actor uuid, p_ip text,
    p_from timestamptz, p_to timestamptz
) RETURNS TABLE (
    o_id bigint, o_occurred_at timestamptz, o_action text, o_actor_id uuid, o_actor_name text,
    o_actor_kind text, o_ip text, o_subject_id uuid, o_subject_name text, o_meta jsonb
) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM app.require_settings_admin();
    RETURN QUERY
    SELECT e.id, e.occurred_at, e.action::text, e.actor_id, a.full_name::text, e.actor_kind::text,
           host(e.ip),
           CASE WHEN e.table_name = 'user' THEN e.row_id END,
           CASE WHEN e.table_name = 'user' THEN s.full_name::text END,
           e.meta - 'db_user'
    FROM public.audit_log e
    LEFT JOIN public."user" a ON a.id = e.actor_id
    LEFT JOIN public."user" s ON e.table_name = 'user' AND s.id = e.row_id
    WHERE e.kind = 'event'
      AND (p_before IS NULL OR e.id < p_before)
      AND (p_action IS NULL OR e.action = p_action)
      AND (p_actor IS NULL OR e.actor_id = p_actor)
      AND (p_ip IS NULL OR host(e.ip) = p_ip)
      AND (p_from IS NULL OR e.occurred_at >= p_from)
      AND (p_to IS NULL OR e.occurred_at < p_to)
    ORDER BY e.id DESC
    LIMIT LEAST(GREATEST(COALESCE(p_limit, 50), 1), 200);
END $$
"""

PURGE_EXPIRED = """
CREATE OR REPLACE FUNCTION app.purge_expired_auth_data() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_tokens integer;
    v_keys integer;
    v_invites integer;
BEGIN
    DELETE FROM public.refresh_token
    WHERE expires_at < now() - interval '1 day' OR revoked_at < now() - interval '1 day';
    GET DIAGNOSTICS v_tokens = ROW_COUNT;
    DELETE FROM public.idempotency_key WHERE expires_at < now();
    GET DIAGNOSTICS v_keys = ROW_COUNT;
    DELETE FROM public.user_invite WHERE expires_at < now() - interval '7 days';
    GET DIAGNOSTICS v_invites = ROW_COUNT;
    RETURN jsonb_build_object('refresh_tokens', v_tokens, 'idempotency_keys', v_keys,
                              'user_invites', v_invites);
END $$
"""

# (SQL, сигнатура, логины с EXECUTE). Порядок важен: context_user нужен остальным.
NEW_FUNCTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (CONTEXT_USER, "app.context_user()", ()),
    (AUTH_PASSWORD_HASH, "app.auth_password_hash()", ("kut_app",)),
    (TOTP_GET, "app.totp_get()", ("kut_app",)),
    (TOTP_SET_PENDING, "app.totp_set_pending(bytea, text)", ("kut_app",)),
    (TOTP_CONFIRM, "app.totp_confirm(bytea[])", ("kut_app",)),
    (RECOVERY_CODE_USE, "app.recovery_code_use(bytea)", ("kut_app",)),
    (REFRESH_ROTATE, "app.refresh_rotate(bytea, bytea, interval)", ("kut_app",)),
    (ADMIN_CREATE_USER, "app.admin_create_user(text, text, text, uuid, uuid)", ("kut_app",)),
    (ADMIN_ISSUE_INVITE, "app.admin_issue_invite(uuid, bytea, interval)", ("kut_app",)),
    (INVITE_CONSUME, "app.invite_consume(bytea, text)", ("kut_app",)),
    (ADMIN_RESET_TOTP, "app.admin_reset_totp(uuid)", ("kut_app",)),
    (
        ADMIN_AUTH_EVENTS,
        "app.admin_auth_events(integer, bigint, text, uuid, text, timestamptz, timestamptz)",
        ("kut_app",),
    ),
)

# Сигнатуры, которые заменяются новыми (старые определения возвращает downgrade из ревизии S1b).
REPLACED_SIGNATURES = (
    "app.totp_get(uuid)",
    "app.totp_set_pending(uuid, bytea, text)",
    "app.totp_confirm(uuid, bytea[])",
    "app.recovery_code_use(uuid, bytea)",
    "app.auth_password_hash(uuid)",
    "app.refresh_rotate(bytea, bytea)",
)


def _s1b() -> object:
    """Модуль ревизии S1b: его определения функций нужны downgrade, дублировать их незачем."""
    path = next(pathlib.Path(__file__).parent.glob("*a56217376426*.py"))
    spec = importlib.util.spec_from_file_location("s1b_revision", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    op.create_table(
        "user_invite",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("issued_by", sa.Uuid(), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_user_invite_user_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_invite")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_invite_token_hash")),
    )
    op.create_index("ix_user_invite_user_id", "user_invite", ["user_id"], unique=False)

    # Служебная таблица: ни одному логину прав нет, как у refresh_token и recovery_code.
    force_rls("user_invite")
    op.execute(
        "CREATE POLICY user_invite_auth_only ON user_invite FOR ALL TO kut_auth "
        "USING (true) WITH CHECK (true)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_invite TO kut_auth")
    op.execute('GRANT INSERT ON "user" TO kut_auth')  # admin_create_user
    op.execute("GRANT SELECT ON counterparty TO kut_auth")  # проверка контрагента при создании
    op.execute("GRANT SELECT ON audit_log TO kut_auth")  # admin_auth_events: только kind = 'event'

    # kut_worker: секретные колонки только на запись (сид и тесты пишут, читать не нужно никому).
    op.execute('REVOKE SELECT ON "user" FROM kut_worker')
    op.execute(f'GRANT SELECT ({", ".join(WORKER_READABLE)}) ON "user" TO kut_worker')

    op.execute("SET LOCAL ROLE kut_auth")
    for signature in REPLACED_SIGNATURES:
        op.execute(f"DROP FUNCTION {signature}")
    for sql, signature, logins in NEW_FUNCTIONS:
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        for login in logins:
            op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {login}")
    op.execute(PURGE_EXPIRED)
    op.execute("RESET ROLE")


def downgrade() -> None:
    s1b = _s1b()
    old = {signature: sql for sql, signature, _logins in s1b.FUNCTIONS}  # type: ignore[attr-defined]

    op.execute("SET LOCAL ROLE kut_auth")
    for _sql, signature, _logins in reversed(NEW_FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
    for signature in REPLACED_SIGNATURES:
        op.execute(old[signature])
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO kut_app")
    op.execute(
        old["app.purge_expired_auth_data()"].replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
    )
    op.execute("RESET ROLE")

    op.execute('REVOKE SELECT ON "user" FROM kut_worker')
    op.execute('GRANT SELECT ON "user" TO kut_worker')
    op.execute("REVOKE SELECT ON audit_log FROM kut_auth")
    op.execute("REVOKE SELECT ON counterparty FROM kut_auth")
    op.execute('REVOKE INSERT ON "user" FROM kut_auth')
    op.execute("DROP POLICY user_invite_auth_only ON user_invite")
    op.drop_table("user_invite")
