"""rls: context functions, policies, grants

Revision ID: 21bdf57acda3
Revises: 738b6eb6ef1b
Create Date: 2026-10-01 09:32:10.000000

"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import force_rls, grant

revision: str = "21bdf57acda3"
down_revision: str | Sequence[str] | None = "738b6eb6ef1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGINS = "kut_app, kut_portal, kut_worker"
READ_WRITE = "SELECT, INSERT, UPDATE"

# Значение GUC после транзакции остаётся '' (а не NULL), поэтому везде NULLIF: пустой контекст
# превращается в NULL / пустой массив / false, и политика отдаёт 0 строк (fail-closed).
CONTEXT_FUNCTIONS = {
    "user_id": ("uuid", "NULLIF(current_setting('app.user_id', true), '')::uuid"),
    "request_id": ("uuid", "NULLIF(current_setting('app.request_id', true), '')::uuid"),
    "counterparty_id": ("uuid", "NULLIF(current_setting('app.counterparty_id', true), '')::uuid"),
    "is_internal": (
        "boolean",
        "COALESCE(NULLIF(current_setting('app.is_internal', true), '')::boolean, false)",
    ),
    "project_ids": (
        "uuid[]",
        "COALESCE(string_to_array(NULLIF(current_setting('app.project_ids', true), ''),"
        " ',')::uuid[], '{}'::uuid[])",
    ),
    "legal_entity_ids": (
        "uuid[]",
        "COALESCE(string_to_array(NULLIF(current_setting('app.legal_entity_ids', true), ''),"
        " ',')::uuid[], '{}'::uuid[])",
    ),
    "actor_kind": (
        "text",
        "CASE WHEN app.user_id() IS NULL THEN 'system'"
        " WHEN app.is_internal() THEN 'user' ELSE 'external' END",
    ),
}

# (SELECT f()) вычисляется один раз на запрос (InitPlan), а не на строку. Приведение ::uuid[] нужно:
# без него `= ANY ((SELECT ...))` разбирается как подзапрос-набор и падает с uuid = uuid[].
INTERNAL = "(SELECT app.is_internal())"
USER_ID = "(SELECT app.user_id())"
COUNTERPARTY = "(SELECT app.counterparty_id())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL_ENTITIES = "((SELECT app.legal_entity_ids())::uuid[])"

# (таблица, политика, команда, логин, USING, WITH CHECK).
# kut_worker политик не имеет: у него BYPASSRLS. kut_owner политик не имеет намеренно:
# с FORCE он видит 0 строк и не может писать, если приложение по ошибке подключится под ним.
POLICIES: tuple[tuple[str, str, str, str, str | None, str | None], ...] = (
    (
        "legal_entity",
        "legal_entity_select_app",
        "SELECT",
        "kut_app",
        f"{INTERNAL} AND (id = ANY {LEGAL_ENTITIES}"
        " OR id IN (SELECT legal_entity_id FROM project))",
        None,
    ),
    ("legal_entity", "legal_entity_insert_app", "INSERT", "kut_app", None, INTERNAL),
    (
        "legal_entity",
        "legal_entity_update_app",
        "UPDATE",
        "kut_app",
        f"{INTERNAL} AND id = ANY {LEGAL_ENTITIES}",
        f"{INTERNAL} AND id = ANY {LEGAL_ENTITIES}",
    ),
    ("counterparty", "counterparty_select_app", "SELECT", "kut_app", INTERNAL, None),
    (
        "counterparty",
        "counterparty_select_portal",
        "SELECT",
        "kut_portal",
        f"id = {COUNTERPARTY} AND deleted_at IS NULL",
        None,
    ),
    ("counterparty", "counterparty_insert_app", "INSERT", "kut_app", None, INTERNAL),
    ("counterparty", "counterparty_update_app", "UPDATE", "kut_app", INTERNAL, INTERNAL),
    ("user", "user_select_app", "SELECT", "kut_app", f"{INTERNAL} OR id = {USER_ID}", None),
    (
        "user",
        "user_select_portal",
        "SELECT",
        "kut_portal",
        f"id = {USER_ID} AND deleted_at IS NULL",
        None,
    ),
    ("user", "user_insert_app", "INSERT", "kut_app", None, INTERNAL),
    ("user", "user_update_app", "UPDATE", "kut_app", INTERNAL, INTERNAL),
    (
        "project",
        "project_select_app",
        "SELECT",
        "kut_app",
        f"{INTERNAL} AND id = ANY {PROJECTS}",
        None,
    ),
    (
        "project",
        "project_select_portal",
        "SELECT",
        "kut_portal",
        f"id = ANY {PROJECTS} AND deleted_at IS NULL",
        None,
    ),
    (
        "project",
        "project_insert_app",
        "INSERT",
        "kut_app",
        None,
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL_ENTITIES}",
    ),
    (
        "project",
        "project_update_app",
        "UPDATE",
        "kut_app",
        f"{INTERNAL} AND id = ANY {PROJECTS}",
        f"{INTERNAL} AND id = ANY {PROJECTS}",
    ),
    (
        "user_project_access",
        "user_project_access_select_app",
        "SELECT",
        "kut_app",
        f"user_id = {USER_ID} OR ({INTERNAL} AND project_id = ANY {PROJECTS})",
        None,
    ),
    (
        "user_project_access",
        "user_project_access_select_portal",
        "SELECT",
        "kut_portal",
        f"user_id = {USER_ID} AND deleted_at IS NULL",
        None,
    ),
    (
        "user_project_access",
        "user_project_access_insert_app",
        "INSERT",
        "kut_app",
        None,
        f"{INTERNAL} AND project_id = ANY {PROJECTS}",
    ),
    (
        "user_project_access",
        "user_project_access_update_app",
        "UPDATE",
        "kut_app",
        f"{INTERNAL} AND project_id = ANY {PROJECTS}",
        f"{INTERNAL} AND project_id = ANY {PROJECTS}",
    ),
    (
        "user_legal_entity_access",
        "user_legal_entity_access_select_app",
        "SELECT",
        "kut_app",
        f"user_id = {USER_ID} OR ({INTERNAL} AND legal_entity_id = ANY {LEGAL_ENTITIES})",
        None,
    ),
    (
        "user_legal_entity_access",
        "user_legal_entity_access_insert_app",
        "INSERT",
        "kut_app",
        None,
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL_ENTITIES}",
    ),
    (
        "user_legal_entity_access",
        "user_legal_entity_access_update_app",
        "UPDATE",
        "kut_app",
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL_ENTITIES}",
        f"{INTERNAL} AND legal_entity_id = ANY {LEGAL_ENTITIES}",
    ),
)

RLS_TABLES = (
    "legal_entity",
    "counterparty",
    "user",
    "project",
    "user_project_access",
    "user_legal_entity_access",
)
CATALOG_TABLES = ("role", "permission", "role_permission")
USER_PORTAL_COLUMNS = (
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
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA app")
    # Новые функции kut_owner по умолчанию недоступны PUBLIC; доступ выдаётся явно.
    op.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public, app TO {LOGINS}")

    for name, (returns, body) in CONTEXT_FUNCTIONS.items():
        op.execute(
            f"CREATE FUNCTION app.{name}() RETURNS {returns} LANGUAGE sql STABLE"
            f" AS $$ SELECT {body} $$"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{name}() TO {LOGINS}")

    for table in RLS_TABLES:
        force_rls(table)
    for table, name, command, login, using, check in POLICIES:
        clauses = ""
        if using:
            clauses += f" USING ({using})"
        if check:
            clauses += f" WITH CHECK ({check})"
        op.execute(f'CREATE POLICY {name} ON "{table}" FOR {command} TO {login}{clauses}')

    grant("legal_entity", app=READ_WRITE, worker=READ_WRITE)
    grant("counterparty", app=READ_WRITE, portal="SELECT", worker=READ_WRITE)
    for table in CATALOG_TABLES:
        grant(table, app="SELECT", portal="SELECT", worker="SELECT")
    # password_hash внешнему пулу не отдаётся ни при каких условиях.
    grant("user", app=READ_WRITE, worker=READ_WRITE, portal_columns=USER_PORTAL_COLUMNS)
    grant("project", app=READ_WRITE, portal="SELECT", worker=READ_WRITE)
    grant("user_project_access", app=READ_WRITE, portal="SELECT", worker=READ_WRITE)
    grant("user_legal_entity_access", app=READ_WRITE, worker=READ_WRITE)


def downgrade() -> None:
    for table, name, _command, _login, _using, _check in reversed(POLICIES):
        op.execute(f'DROP POLICY IF EXISTS {name} ON "{table}"')
    for table in RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    for table in (*RLS_TABLES, *CATALOG_TABLES):
        op.execute(f'REVOKE ALL ON "{table}" FROM {LOGINS}')

    for name in CONTEXT_FUNCTIONS:
        op.execute(f"DROP FUNCTION app.{name}()")
    op.execute("DROP SCHEMA app")
    op.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {LOGINS}")
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
