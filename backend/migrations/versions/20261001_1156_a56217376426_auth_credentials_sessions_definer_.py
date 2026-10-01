"""auth: credentials, sessions, definer functions, user visibility

Revision ID: a56217376426
Revises: 942a8dd5d2f2
Create Date: 2026-10-01 11:56:00.000000

Учётные данные закрываются от kut_app: пароль, секрет TOTP, роль и признак активности читаются и
меняются только SECURITY DEFINER-функциями, владелец которых kut_auth (NOLOGIN, BYPASSRLS; роль и
право kut_owner делать SET ROLE создаёт db/bootstrap.sql). Здесь же таблицы сессий, кодов
восстановления и идемпотентности, а также политика видимости пользователей (решение PO после S1a).
Ревизии S1a не менялись: всё остальное в них остаётся как было.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.helpers import force_rls

revision: str = "a56217376426"
down_revision: str | Sequence[str] | None = "942a8dd5d2f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGINS = "kut_app, kut_portal, kut_worker"
CONTEXT_FUNCTIONS = (
    "user_id()",
    "request_id()",
    "counterparty_id()",
    "is_internal()",
    "project_ids()",
    "legal_entity_ids()",
    "actor_kind()",
)

# Колонки user, которые kut_app видит и пишет сам. Пароль, секреты TOTP, роль, признак активности,
# привязка к контрагенту, счётчик role_ver и deleted_at меняются только функциями app.*.
USER_READABLE = (
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
USER_WRITABLE = ("phone", "email", "full_name", "updated_at", "updated_by")
USER_HIDDEN_FROM_JOURNAL = "password_hash,totp_secret_enc"

SEES_ALL_USERS = """
CREATE FUNCTION app.sees_all_users() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT COALESCE(NULLIF(current_setting('app.sees_all_users', true), '')::boolean, false) $$
"""

# Пользователя видят: он сам; внутренние роли - тех, у кого есть общий объект; всех - только роли
# scope=group и роль с settings:admin (флаг sees_all_users строит сервер при выдаче контекста).
# Портальная политика (user_select_portal) не меняется.
USER_SELECT_POLICY = """
CREATE POLICY user_select_app ON "user" FOR SELECT TO kut_app USING (
    id = (SELECT app.user_id())
    OR ((SELECT app.is_internal())
        AND ((SELECT app.sees_all_users())
             OR EXISTS (SELECT 1 FROM user_project_access upa
                        WHERE upa.user_id = "user".id AND upa.deleted_at IS NULL
                          AND upa.project_id = ANY ((SELECT app.project_ids())::uuid[])))))
"""
USER_SELECT_POLICY_S1A = """
CREATE POLICY user_select_app ON "user" FOR SELECT TO kut_app USING (
    (SELECT app.is_internal()) OR id = (SELECT app.user_id()))
"""

AUDIT_EVENT = """
CREATE FUNCTION app.audit_event(
    p_action text, p_table text, p_row_id uuid, p_actor uuid, p_ip inet, p_meta jsonb
) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    INSERT INTO public.audit_log
        (kind, action, actor_id, actor_kind, request_id, ip, table_name, row_id, meta)
    VALUES ('event', p_action, p_actor,
            CASE WHEN p_actor IS NULL THEN 'system'
                 WHEN EXISTS (SELECT 1 FROM public."user" usr JOIN public.role r ON r.id = usr.role_id
                              WHERE usr.id = p_actor AND r.scope = 'counterparty') THEN 'external'
                 ELSE 'user' END,
            app.request_id(), p_ip, p_table, p_row_id,
            COALESCE(p_meta, '{}'::jsonb) || jsonb_build_object('db_user', session_user))
$$
"""

AUTH_LOGIN_LOOKUP = """
CREATE FUNCTION app.auth_login_lookup(p_login text) RETURNS TABLE (
    o_user_id uuid, o_password_hash text, o_is_active boolean, o_requires_2fa boolean,
    o_totp_confirmed boolean, o_role_scope text, o_role_code text
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT usr.id, usr.password_hash, usr.is_active, r.requires_2fa,
           usr.totp_confirmed_at IS NOT NULL, r.scope, r.code
    FROM public."user" usr JOIN public.role r ON r.id = usr.role_id
    WHERE usr.deleted_at IS NULL AND r.deleted_at IS NULL
      AND (usr.email = p_login OR usr.phone = p_login)
    LIMIT 1
$$
"""

AUTH_PASSWORD_HASH = """
CREATE FUNCTION app.auth_password_hash(p_user_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT usr.password_hash FROM public."user" usr
    WHERE usr.id = p_user_id AND usr.deleted_at IS NULL
$$
"""

# Контекст запроса целиком: для scope=group все не удалённые объекты и юрлица, для остальных
# назначенные в user_project_access / user_legal_entity_access.
AUTH_CONTEXT = """
CREATE FUNCTION app.auth_context(p_user_id uuid) RETURNS TABLE (
    o_user_id uuid, o_full_name text, o_is_active boolean, o_role_ver integer, o_role_code text,
    o_role_scope text, o_requires_2fa boolean, o_counterparty_id uuid, o_totp_confirmed boolean,
    o_permissions text[], o_project_ids uuid[], o_legal_entity_ids uuid[]
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH u AS (
        SELECT usr.id, usr.full_name, usr.is_active, usr.role_ver, usr.role_id, usr.counterparty_id,
               usr.totp_confirmed_at, r.code AS role_code, r.scope AS role_scope, r.requires_2fa
        FROM public."user" usr JOIN public.role r ON r.id = usr.role_id
        WHERE usr.id = p_user_id AND usr.deleted_at IS NULL
    )
    SELECT u.id, u.full_name, u.is_active, u.role_ver, u.role_code, u.role_scope, u.requires_2fa,
           u.counterparty_id, u.totp_confirmed_at IS NOT NULL,
           COALESCE((SELECT array_agg(DISTINCT p.module || ':' || p.action
                                               || COALESCE(':' || p.qualifier, ''))
                     FROM public.role_permission rp JOIN public.permission p ON p.id = rp.permission_id
                     WHERE rp.role_id = u.role_id AND rp.deleted_at IS NULL AND p.deleted_at IS NULL),
                    '{}'::text[]),
           COALESCE(CASE WHEN u.role_scope = 'group'
                    THEN (SELECT array_agg(pr.id) FROM public.project pr WHERE pr.deleted_at IS NULL)
                    ELSE (SELECT array_agg(DISTINCT upa.project_id)
                          FROM public.user_project_access upa
                          JOIN public.project pr ON pr.id = upa.project_id
                          WHERE upa.user_id = u.id AND upa.deleted_at IS NULL
                            AND pr.deleted_at IS NULL) END, '{}'::uuid[]),
           COALESCE(CASE WHEN u.role_scope = 'group'
                    THEN (SELECT array_agg(le.id) FROM public.legal_entity le
                          WHERE le.deleted_at IS NULL)
                    ELSE (SELECT array_agg(DISTINCT ula.legal_entity_id)
                          FROM public.user_legal_entity_access ula
                          JOIN public.legal_entity le ON le.id = ula.legal_entity_id
                          WHERE ula.user_id = u.id AND ula.deleted_at IS NULL
                            AND le.deleted_at IS NULL) END, '{}'::uuid[])
    FROM u
$$
"""

REFRESH_ISSUE = """
CREATE FUNCTION app.refresh_issue(
    p_user_id uuid, p_family_id uuid, p_hash bytea, p_ttl interval, p_amr text[]
) RETURNS timestamptz LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    INSERT INTO public.refresh_token (user_id, family_id, token_hash, amr, expires_at)
    VALUES (p_user_id, p_family_id, p_hash, p_amr, now() + p_ttl)
    RETURNING expires_at
$$
"""

# Ротация: старый токен гасится, выдаётся новый в той же семье. Повторное предъявление уже
# использованного токена отзывает всю семью и пишет событие refresh_reuse (признак кражи).
REFRESH_ROTATE = """
CREATE FUNCTION app.refresh_rotate(p_old bytea, p_new bytea) RETURNS TABLE (
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
    IF v_token.used_at IS NOT NULL THEN
        UPDATE public.refresh_token SET revoked_at = now(), revoked_reason = 'reuse'
        WHERE family_id = v_token.family_id AND revoked_at IS NULL;
        PERFORM app.audit_event('refresh_reuse', 'refresh_token', v_token.family_id,
                                v_token.user_id, NULL,
                                jsonb_build_object('family_id', v_token.family_id));
        RETURN QUERY SELECT 'reused'::text, v_token.user_id, v_token.family_id, v_token.amr::text[],
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
    -- Срок нового токена равен сроку предъявленного: 14 или 30 дней сохраняются при ротации.
    v_ttl := v_token.expires_at - v_token.issued_at;
    UPDATE public.refresh_token SET used_at = now() WHERE id = v_token.id;
    INSERT INTO public.refresh_token (user_id, family_id, token_hash, amr, expires_at)
    VALUES (v_token.user_id, v_token.family_id, p_new, v_token.amr, now() + v_ttl);
    RETURN QUERY SELECT 'ok'::text, v_token.user_id, v_token.family_id, v_token.amr::text[], now() + v_ttl;
END $$
"""

REFRESH_LOGOUT = """
CREATE FUNCTION app.refresh_logout(p_hash bytea) RETURNS TABLE (o_user_id uuid, o_family_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_token public.refresh_token%ROWTYPE;
BEGIN
    SELECT * INTO v_token FROM public.refresh_token WHERE token_hash = p_hash;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    UPDATE public.refresh_token SET revoked_at = now(), revoked_reason = 'logout'
    WHERE family_id = v_token.family_id AND revoked_at IS NULL;
    -- Повторный выход по уже закрытой сессии не пишется в журнал второй раз.
    IF FOUND THEN
        PERFORM app.audit_event('logout', 'refresh_token', v_token.family_id, v_token.user_id,
                                NULL, NULL);
    END IF;
    RETURN QUERY SELECT v_token.user_id, v_token.family_id;
END $$
"""

# Внутренний помощник (приложению не выдаётся): отзывает сессии пользователя и возвращает их id,
# чтобы вызывающий сервис внёс их в denylist Redis.
REFRESH_REVOKE_USER = """
CREATE FUNCTION app.refresh_revoke_user(p_user_id uuid, p_keep_family uuid, p_reason text)
RETURNS uuid[] LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH revoked AS (
        UPDATE public.refresh_token SET revoked_at = now(), revoked_reason = p_reason
        WHERE user_id = p_user_id AND revoked_at IS NULL
          AND family_id IS DISTINCT FROM p_keep_family
        RETURNING family_id)
    SELECT COALESCE(array_agg(DISTINCT family_id), '{}'::uuid[]) FROM revoked
$$
"""

TOTP_GET = """
CREATE FUNCTION app.totp_get(p_user_id uuid) RETURNS TABLE (
    o_secret_enc bytea, o_key_id text, o_confirmed boolean
) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT usr.totp_secret_enc, usr.totp_key_id, usr.totp_confirmed_at IS NOT NULL
    FROM public."user" usr WHERE usr.id = p_user_id AND usr.deleted_at IS NULL
$$
"""

TOTP_SET_PENDING = """
CREATE FUNCTION app.totp_set_pending(p_user_id uuid, p_secret_enc bytea, p_key_id text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH changed AS (
        UPDATE public."user" SET totp_secret_enc = p_secret_enc, totp_key_id = p_key_id
        WHERE id = p_user_id AND deleted_at IS NULL AND totp_confirmed_at IS NULL
        RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM changed)
$$
"""

TOTP_CONFIRM = """
CREATE FUNCTION app.totp_confirm(p_user_id uuid, p_code_hashes bytea[]) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public."user" SET totp_confirmed_at = now()
    WHERE id = p_user_id AND deleted_at IS NULL
      AND totp_secret_enc IS NOT NULL AND totp_confirmed_at IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    DELETE FROM public.recovery_code WHERE user_id = p_user_id;
    INSERT INTO public.recovery_code (user_id, code_hash)
    SELECT p_user_id, code FROM unnest(p_code_hashes) AS code;
    PERFORM app.audit_event('mfa_enabled', 'user', p_user_id, p_user_id, NULL, NULL);
    RETURN true;
END $$
"""

RECOVERY_CODE_USE = """
CREATE FUNCTION app.recovery_code_use(p_user_id uuid, p_hash bytea) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.recovery_code SET used_at = now()
    WHERE user_id = p_user_id AND code_hash = p_hash AND used_at IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM app.audit_event(
        'recovery_code_used', 'user', p_user_id, p_user_id, NULL,
        jsonb_build_object('left', (SELECT count(*) FROM public.recovery_code
                                    WHERE user_id = p_user_id AND used_at IS NULL)));
    RETURN true;
END $$
"""

CHANGE_PASSWORD = """
CREATE FUNCTION app.change_password(p_user_id uuid, p_new_hash text, p_keep_family uuid)
RETURNS uuid[] LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_families uuid[];
BEGIN
    -- Пароль меняется только из контекста самого пользователя: ошибка кода не заденет чужую учётку.
    IF app.user_id() IS DISTINCT FROM p_user_id THEN
        RAISE EXCEPTION 'password can only be changed by its owner'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE public."user" SET password_hash = p_new_hash, password_changed_at = now(),
                             updated_by = p_user_id
    WHERE id = p_user_id AND deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    v_families := app.refresh_revoke_user(p_user_id, p_keep_family, 'password_changed');
    PERFORM app.audit_event('password_changed', 'user', p_user_id, p_user_id, NULL,
                            jsonb_build_object('revoked_sessions', cardinality(v_families)));
    RETURN v_families;
END $$
"""

# Право settings:admin проверяется по контексту запроса (app.user_id()), а не по параметру.
REQUIRE_SETTINGS_ADMIN = """
CREATE FUNCTION app.require_settings_admin() RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.user_id();
BEGIN
    IF v_actor IS NULL OR NOT EXISTS (
        SELECT 1 FROM public."user" usr
        JOIN public.role_permission rp ON rp.role_id = usr.role_id AND rp.deleted_at IS NULL
        JOIN public.permission p ON p.id = rp.permission_id AND p.deleted_at IS NULL
        WHERE usr.id = v_actor AND usr.is_active AND usr.deleted_at IS NULL
          AND p.module = 'settings' AND p.action = 'admin' AND p.qualifier IS NULL
    ) THEN
        RAISE EXCEPTION 'settings:admin permission required' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN v_actor;
END $$
"""

ASSIGN_ROLE = """
CREATE FUNCTION app.assign_role(p_user_id uuid, p_role_id uuid)
RETURNS TABLE (o_role_ver integer, o_families uuid[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.require_settings_admin();
    v_old uuid;
    v_ver integer;
    v_families uuid[];
BEGIN
    IF p_user_id = v_actor THEN
        RAISE EXCEPTION 'cannot change own role' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT usr.role_id INTO v_old FROM public."user" usr
    WHERE usr.id = p_user_id AND usr.deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.role r WHERE r.id = p_role_id AND r.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'role not found' USING ERRCODE = 'no_data_found';
    END IF;
    UPDATE public."user" SET role_id = p_role_id, role_ver = role_ver + 1, updated_by = v_actor
    WHERE id = p_user_id RETURNING role_ver INTO v_ver;
    v_families := app.refresh_revoke_user(p_user_id, NULL, 'role_changed');
    PERFORM app.audit_event('role_assigned', 'user', p_user_id, v_actor, NULL,
                            jsonb_build_object('old_role_id', v_old, 'new_role_id', p_role_id,
                                               'role_ver', v_ver));
    RETURN QUERY SELECT v_ver, v_families;
END $$
"""

SET_USER_ACTIVE = """
CREATE FUNCTION app.set_user_active(p_user_id uuid, p_active boolean)
RETURNS TABLE (o_role_ver integer, o_families uuid[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_actor uuid := app.require_settings_admin();
    v_ver integer;
    v_families uuid[];
BEGIN
    IF p_user_id = v_actor THEN
        RAISE EXCEPTION 'cannot change own activity' USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE public."user" SET is_active = p_active, role_ver = role_ver + 1, updated_by = v_actor
    WHERE id = p_user_id AND deleted_at IS NULL RETURNING role_ver INTO v_ver;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user not found' USING ERRCODE = 'no_data_found';
    END IF;
    v_families := app.refresh_revoke_user(p_user_id, NULL,
                                          CASE WHEN p_active THEN 'reactivated' ELSE 'deactivated' END);
    PERFORM app.audit_event(CASE WHEN p_active THEN 'user_activated' ELSE 'user_deactivated' END,
                            'user', p_user_id, v_actor, NULL, jsonb_build_object('role_ver', v_ver));
    RETURN QUERY SELECT v_ver, v_families;
END $$
"""

PURGE_EXPIRED = """
CREATE FUNCTION app.purge_expired_auth_data() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_tokens integer;
    v_keys integer;
BEGIN
    DELETE FROM public.refresh_token
    WHERE expires_at < now() - interval '1 day' OR revoked_at < now() - interval '1 day';
    GET DIAGNOSTICS v_tokens = ROW_COUNT;
    DELETE FROM public.idempotency_key WHERE expires_at < now();
    GET DIAGNOSTICS v_keys = ROW_COUNT;
    RETURN jsonb_build_object('refresh_tokens', v_tokens, 'idempotency_keys', v_keys);
END $$
"""

# (SQL, сигнатура для GRANT, логины с правом EXECUTE). Порядок важен: audit_event нужен ротации.
FUNCTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (AUDIT_EVENT, "app.audit_event(text, text, uuid, uuid, inet, jsonb)", ("kut_app",)),
    (AUTH_LOGIN_LOOKUP, "app.auth_login_lookup(text)", ("kut_app",)),
    (AUTH_PASSWORD_HASH, "app.auth_password_hash(uuid)", ("kut_app",)),
    (AUTH_CONTEXT, "app.auth_context(uuid)", ("kut_app",)),
    (REFRESH_ISSUE, "app.refresh_issue(uuid, uuid, bytea, interval, text[])", ("kut_app",)),
    (REFRESH_ROTATE, "app.refresh_rotate(bytea, bytea)", ("kut_app",)),
    (REFRESH_LOGOUT, "app.refresh_logout(bytea)", ("kut_app",)),
    (REFRESH_REVOKE_USER, "app.refresh_revoke_user(uuid, uuid, text)", ()),
    (TOTP_GET, "app.totp_get(uuid)", ("kut_app",)),
    (TOTP_SET_PENDING, "app.totp_set_pending(uuid, bytea, text)", ("kut_app",)),
    (TOTP_CONFIRM, "app.totp_confirm(uuid, bytea[])", ("kut_app",)),
    (RECOVERY_CODE_USE, "app.recovery_code_use(uuid, bytea)", ("kut_app",)),
    (CHANGE_PASSWORD, "app.change_password(uuid, text, uuid)", ("kut_app",)),
    (REQUIRE_SETTINGS_ADMIN, "app.require_settings_admin()", ()),
    (ASSIGN_ROLE, "app.assign_role(uuid, uuid)", ("kut_app",)),
    (SET_USER_ACTIVE, "app.set_user_active(uuid, boolean)", ("kut_app",)),
    (PURGE_EXPIRED, "app.purge_expired_auth_data()", ("kut_worker",)),
)


def _audit_triggers_on_user(hidden: str) -> None:
    args = f"'', '', '{hidden}'"
    op.execute(
        'CREATE TRIGGER audit_insert AFTER INSERT ON "user" REFERENCING NEW TABLE AS new_rows '
        f"FOR EACH STATEMENT EXECUTE FUNCTION app.audit_insert({args})"
    )
    op.execute(
        'CREATE TRIGGER audit_update AFTER UPDATE ON "user" '
        "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows "
        f"FOR EACH STATEMENT EXECUTE FUNCTION app.audit_update({args})"
    )
    op.execute(
        'CREATE TRIGGER audit_delete AFTER DELETE ON "user" REFERENCING OLD TABLE AS old_rows '
        f"FOR EACH STATEMENT EXECUTE FUNCTION app.audit_delete({args})"
    )


def _drop_audit_triggers_on_user() -> None:
    for name in ("audit_insert", "audit_update", "audit_delete"):
        op.execute(f'DROP TRIGGER {name} ON "user"')


def upgrade() -> None:
    # --- колонки и таблицы ---
    op.add_column("user", sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True))
    op.add_column("user", sa.Column("totp_key_id", sa.String(), nullable=True))
    op.add_column("user", sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "user", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "refresh_token",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("amr", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_refresh_token_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_token_token_hash")),
    )
    op.create_index("ix_refresh_token_user_id", "refresh_token", ["user_id"], unique=False)
    op.create_index("ix_refresh_token_family_id", "refresh_token", ["family_id"], unique=False)
    op.create_index("ix_refresh_token_expires_at", "refresh_token", ["expires_at"], unique=False)

    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_recovery_code_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_code")),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_code_user_id_code_hash"),
    )
    op.create_index("ix_recovery_code_user_id", "recovery_code", ["user_id"], unique=False)
    op.create_index(
        "ix_recovery_code_unused",
        "recovery_code",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("used_at IS NULL"),
    )

    op.create_table(
        "idempotency_key",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "stored_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_idempotency_key_user_id_user")
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_idempotency_key")),
    )
    op.create_index(
        "ix_idempotency_key_expires_at", "idempotency_key", ["expires_at"], unique=False
    )

    # --- права и политики (выполняет kut_owner) ---
    op.execute(SEES_ALL_USERS)
    op.execute(f"GRANT EXECUTE ON FUNCTION app.sees_all_users() TO {LOGINS}, kut_auth")
    for function in CONTEXT_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{function} TO kut_auth")

    op.execute("GRANT USAGE ON SCHEMA public, app TO kut_auth")
    op.execute("GRANT CREATE ON SCHEMA app TO kut_auth")
    op.execute('GRANT SELECT, UPDATE ON "user" TO kut_auth')
    for table in (
        "role",
        "permission",
        "role_permission",
        "project",
        "legal_entity",
        "user_project_access",
        "user_legal_entity_access",
    ):
        op.execute(f'GRANT SELECT ON "{table}" TO kut_auth')
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON refresh_token TO kut_auth")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recovery_code TO kut_auth")
    op.execute("GRANT SELECT, DELETE ON idempotency_key TO kut_auth")
    op.execute("GRANT INSERT ON audit_log TO kut_auth")

    # Служебные таблицы: ни одному логину прав нет, RLS включён как страховка на случай ошибочной выдачи.
    for table in ("refresh_token", "recovery_code"):
        force_rls(table)
        op.execute(
            f"CREATE POLICY {table}_auth_only ON {table} FOR ALL TO kut_auth "
            "USING (true) WITH CHECK (true)"
        )
    force_rls("idempotency_key")
    op.execute(
        "CREATE POLICY idempotency_key_select_app ON idempotency_key FOR SELECT TO kut_app "
        "USING (user_id = (SELECT app.user_id()))"
    )
    op.execute(
        "CREATE POLICY idempotency_key_insert_app ON idempotency_key FOR INSERT TO kut_app "
        "WITH CHECK (user_id = (SELECT app.user_id()))"
    )
    # Ответ дописывается в той же транзакции, пока запись «в работе» (response_code = 0);
    # завершённая запись для приложения неизменяема.
    op.execute(
        "CREATE POLICY idempotency_key_update_app ON idempotency_key FOR UPDATE TO kut_app "
        "USING (user_id = (SELECT app.user_id()) AND response_code = 0) "
        "WITH CHECK (user_id = (SELECT app.user_id()))"
    )
    op.execute("GRANT SELECT, INSERT ON idempotency_key TO kut_app")
    op.execute("GRANT UPDATE (response_code, response_body) ON idempotency_key TO kut_app")

    op.execute('DROP POLICY user_select_app ON "user"')
    op.execute(USER_SELECT_POLICY)

    # kut_app: вместо табличных SELECT/INSERT/UPDATE - списки колонок; INSERT отзывается совсем
    # (создание пользователя появится как функция администратора в S1c).
    op.execute('REVOKE SELECT, INSERT, UPDATE ON "user" FROM kut_app')
    op.execute(f'GRANT SELECT ({", ".join(USER_READABLE)}) ON "user" TO kut_app')
    op.execute(f'GRANT UPDATE ({", ".join(USER_WRITABLE)}) ON "user" TO kut_app')

    # Шифртекст секрета TOTP не должен попадать в журнал (как и хэш пароля).
    _drop_audit_triggers_on_user()
    _audit_triggers_on_user(USER_HIDDEN_FROM_JOURNAL)

    # --- функции от имени kut_auth ---
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
    for sql, signature, logins in FUNCTIONS:
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        for login in logins:
            op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {login}")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    for _sql, signature, _logins in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
    op.execute("RESET ROLE")

    _drop_audit_triggers_on_user()
    _audit_triggers_on_user("password_hash")

    op.execute('REVOKE ALL ON "user" FROM kut_app')
    op.execute('GRANT SELECT, INSERT, UPDATE ON "user" TO kut_app')
    op.execute('DROP POLICY user_select_app ON "user"')
    op.execute(USER_SELECT_POLICY_S1A)

    for policy, table in (
        ("idempotency_key_update_app", "idempotency_key"),
        ("idempotency_key_insert_app", "idempotency_key"),
        ("idempotency_key_select_app", "idempotency_key"),
        ("recovery_code_auth_only", "recovery_code"),
        ("refresh_token_auth_only", "refresh_token"),
    ):
        op.execute(f"DROP POLICY {policy} ON {table}")
    op.drop_table("idempotency_key")
    op.drop_table("recovery_code")
    op.drop_table("refresh_token")

    for table in (
        "role",
        "permission",
        "role_permission",
        "project",
        "legal_entity",
        "user_project_access",
        "user_legal_entity_access",
        "user",
        "audit_log",
    ):
        op.execute(f'REVOKE ALL ON "{table}" FROM kut_auth')
    op.execute("REVOKE ALL ON SCHEMA public, app FROM kut_auth")
    for function in (*CONTEXT_FUNCTIONS, "sees_all_users()"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION app.{function} FROM kut_auth")
    op.execute("DROP FUNCTION app.sees_all_users()")

    op.drop_column("user", "password_changed_at")
    op.drop_column("user", "totp_confirmed_at")
    op.drop_column("user", "totp_key_id")
    op.drop_column("user", "totp_secret_enc")
