"""audit log: partitioned journal and triggers

Revision ID: 942a8dd5d2f2
Revises: 21bdf57acda3
Create Date: 2026-10-01 09:32:20.000000

"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_audit_triggers, drop_audit_triggers

revision: str = "942a8dd5d2f2"
down_revision: str | Sequence[str] | None = "21bdf57acda3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTITIONS_AHEAD = 24

# (таблица, колонка project_id записи, колонка legal_entity_id записи, секретные колонки)
AUDITED_TABLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("legal_entity", "", "id", ()),
    ("counterparty", "", "", ()),
    ("role", "", "", ()),
    ("permission", "", "", ()),
    ("role_permission", "", "", ()),
    ("user", "", "", ("password_hash",)),
    ("project", "id", "legal_entity_id", ()),
    ("user_project_access", "project_id", "", ()),
    ("user_legal_entity_access", "", "legal_entity_id", ()),
)

INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL_ENTITIES = "((SELECT app.legal_entity_ids())::uuid[])"

CREATE_AUDIT_LOG = """
CREATE TABLE audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL,
    action text NOT NULL,
    actor_id uuid,
    actor_kind text NOT NULL,
    request_id uuid,
    idempotency_key uuid,
    ip inet,
    table_name text,
    row_id uuid,
    legal_entity_id uuid,
    project_id uuid,
    old jsonb,
    new jsonb,
    meta jsonb,
    CONSTRAINT pk_audit_log PRIMARY KEY (occurred_at, id),
    CONSTRAINT ck_audit_log_kind CHECK (kind IN ('data', 'event')),
    CONSTRAINT ck_audit_log_actor_kind CHECK (actor_kind IN ('user', 'system', 'external'))
) PARTITION BY RANGE (occurred_at)
"""

# Месячные партиции создаёт эта функция; границы всегда в UTC. DEFAULT-партиция страхует от потери
# записи, если партицию вовремя не создали: журнал не должен ронять бизнес-операцию.
ENSURE_PARTITIONS = """
CREATE FUNCTION app.ensure_audit_partitions(months_ahead integer DEFAULT 12) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    base timestamp := date_trunc('month', timezone('UTC', now()));
    m timestamp;
    part text;
    created integer := 0;
BEGIN
    FOR i IN -1 .. months_ahead LOOP
        m := base + make_interval(months => i);
        part := 'audit_log_' || to_char(m, 'YYYY_MM');
        IF to_regclass('public.' || quote_ident(part)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.audit_log FOR VALUES FROM (%L) TO (%L)',
                part, timezone('UTC', m)::text, timezone('UTC', m + interval '1 month')::text
            );
            EXECUTE format(
                'REVOKE UPDATE, DELETE, TRUNCATE ON public.%I FROM %I', part, current_user
            );
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END $$
"""

IMMUTABLE_FUNCTION = """
CREATE FUNCTION app.audit_log_immutable() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$
"""

# Аргументы триггера: [0] колонка project_id, [1] колонка legal_entity_id, [2] скрытые колонки.
# Актор, request_id и meta одинаковы для всего оператора: считаются один раз, а не на строку.
AUDIT_INSERT = """
CREATE FUNCTION app.audit_insert() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_hidden text[] := string_to_array(COALESCE(TG_ARGV[2], ''), ',');
    v_project text := NULLIF(TG_ARGV[0], '');
    v_entity text := NULLIF(TG_ARGV[1], '');
    v_actor uuid := app.user_id();
    v_kind text := app.actor_kind();
    v_request uuid := app.request_id();
    v_meta jsonb := jsonb_build_object('db_user', session_user);
BEGIN
    INSERT INTO public.audit_log
        (kind, action, actor_id, actor_kind, request_id, table_name, row_id,
         legal_entity_id, project_id, new, meta)
    SELECT 'data', 'insert', v_actor, v_kind, v_request, TG_TABLE_NAME,
           (r.j ->> 'id')::uuid, (r.j ->> v_entity)::uuid, (r.j ->> v_project)::uuid,
           r.j - v_hidden, v_meta
    FROM (SELECT to_jsonb(n) AS j FROM new_rows n) r;
    RETURN NULL;
END $$
"""

# MATERIALIZED + один jsonb_each на строку: без этого планировщик пересчитывает to_jsonb() для
# каждого ключа, и массовый UPDATE замедляется в разы. updated_at / updated_by в дифф не входят
# (актор и время есть в самой записи журнала); обновление без других изменений строк не пишет.
AUDIT_UPDATE = """
CREATE FUNCTION app.audit_update() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_hidden text[] := string_to_array(COALESCE(TG_ARGV[2], ''), ',')
                       || ARRAY['updated_at', 'updated_by'];
    v_project text := NULLIF(TG_ARGV[0], '');
    v_entity text := NULLIF(TG_ARGV[1], '');
    v_actor uuid := app.user_id();
    v_kind text := app.actor_kind();
    v_request uuid := app.request_id();
    v_meta jsonb := jsonb_build_object('db_user', session_user);
BEGIN
    INSERT INTO public.audit_log
        (kind, action, actor_id, actor_kind, request_id, table_name, row_id,
         legal_entity_id, project_id, old, new, meta)
    WITH pairs AS MATERIALIZED (
        SELECT to_jsonb(o) - v_hidden AS oj, to_jsonb(n) - v_hidden AS nj
        FROM old_rows o JOIN new_rows n ON n.id = o.id
    )
    SELECT 'data',
           CASE WHEN p.oj -> 'deleted_at' = 'null'::jsonb AND p.nj -> 'deleted_at' <> 'null'::jsonb
                    THEN 'soft_delete'
                WHEN p.oj -> 'deleted_at' <> 'null'::jsonb AND p.nj -> 'deleted_at' = 'null'::jsonb
                    THEN 'restore'
                ELSE 'update' END,
           v_actor, v_kind, v_request, TG_TABLE_NAME,
           (p.nj ->> 'id')::uuid, (p.nj ->> v_entity)::uuid, (p.nj ->> v_project)::uuid,
           c.old, c.new, v_meta
    FROM pairs p
    CROSS JOIN LATERAL (
        SELECT jsonb_object_agg(k.key, k.ov) AS old, jsonb_object_agg(k.key, k.nv) AS new
        FROM (SELECT oe.key, oe.value AS ov, p.nj -> oe.key AS nv FROM jsonb_each(p.oj) oe) k
        WHERE k.ov IS DISTINCT FROM k.nv
    ) c
    WHERE c.old IS NOT NULL;
    RETURN NULL;
END $$
"""

AUDIT_DELETE = """
CREATE FUNCTION app.audit_delete() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_hidden text[] := string_to_array(COALESCE(TG_ARGV[2], ''), ',');
    v_project text := NULLIF(TG_ARGV[0], '');
    v_entity text := NULLIF(TG_ARGV[1], '');
    v_actor uuid := app.user_id();
    v_kind text := app.actor_kind();
    v_request uuid := app.request_id();
    v_meta jsonb := jsonb_build_object('db_user', session_user);
BEGIN
    INSERT INTO public.audit_log
        (kind, action, actor_id, actor_kind, request_id, table_name, row_id,
         legal_entity_id, project_id, old, meta)
    SELECT 'data', 'delete', v_actor, v_kind, v_request, TG_TABLE_NAME,
           (r.j ->> 'id')::uuid, (r.j ->> v_entity)::uuid, (r.j ->> v_project)::uuid,
           r.j - v_hidden, v_meta
    FROM (SELECT to_jsonb(o) AS j FROM old_rows o) r;
    RETURN NULL;
END $$
"""


def upgrade() -> None:
    op.execute(CREATE_AUDIT_LOG)
    op.execute("CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log_default FROM CURRENT_USER")
    op.execute("CREATE INDEX ix_audit_log_table_row ON audit_log (table_name, row_id)")
    op.execute("CREATE INDEX ix_audit_log_project_occurred ON audit_log (project_id, occurred_at)")
    op.execute(
        "CREATE INDEX ix_audit_log_legal_entity_occurred"
        " ON audit_log (legal_entity_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_audit_log_occurred_brin ON audit_log USING brin (occurred_at)")

    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    # Писать в журнал может только SECURITY DEFINER-функция триггера (она работает от kut_owner).
    op.execute(
        "CREATE POLICY audit_log_insert_owner ON audit_log"
        " FOR INSERT TO kut_owner WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY audit_log_select_app ON audit_log FOR SELECT TO kut_app USING "
        f"({INTERNAL} AND (project_id = ANY {PROJECTS} OR legal_entity_id = ANY {LEGAL_ENTITIES}))"
    )
    op.execute("GRANT SELECT ON audit_log TO kut_app, kut_worker")
    # Append-only: права отзываются у всех, включая владельца; триггеры ниже закрывают то,
    # что REVOKE не закрывает (суперпользователь, прямой доступ к партиции).
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM CURRENT_USER")

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION app.audit_log_immutable()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION app.audit_log_immutable()"
    )

    op.execute(ENSURE_PARTITIONS)
    op.execute("GRANT EXECUTE ON FUNCTION app.ensure_audit_partitions(integer) TO kut_worker")
    op.execute(f"SELECT app.ensure_audit_partitions({PARTITIONS_AHEAD})")

    for statement in (AUDIT_INSERT, AUDIT_UPDATE, AUDIT_DELETE):
        op.execute(statement)
    for table, project_column, legal_entity_column, hidden in AUDITED_TABLES:
        create_audit_triggers(
            table,
            project_column=project_column,
            legal_entity_column=legal_entity_column,
            hidden_columns=hidden,
        )


def downgrade() -> None:
    for table, *_ in reversed(AUDITED_TABLES):
        drop_audit_triggers(table)
    for function in ("audit_delete()", "audit_update()", "audit_insert()"):
        op.execute(f"DROP FUNCTION app.{function}")
    op.execute("DROP TABLE audit_log")
    op.execute("DROP FUNCTION app.ensure_audit_partitions(integer)")
    op.execute("DROP FUNCTION app.audit_log_immutable()")
