"""project_node (tree of an object) and work (catalog of kinds of work)

Revision ID: b5e8d1a7c963
Revises: a9f3c2d8e471
Create Date: 2026-10-02 12:30:00.000000

Правила дерева живут в БД (ТЗ §5.1), чтобы их не обходил ни один писатель: допустимые типы
потомков, запрет цикла, один объект у родителя и потомка, запрет удалить узел с живыми потомками.
Материализованный путь path (uuid[], от корня до узла, включая сам узел) ведёт триггер.

Права и политики S1 не ослаблены: новые таблицы закрыты FORCE RLS, DELETE никому не выдан, внешнему
пулу kut_portal выдан явный список колонок узла без ответственных и служебных полей.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.helpers import create_audit_triggers, drop_audit_triggers, force_rls, grant

revision: str = "b5e8d1a7c963"
down_revision: str | Sequence[str] | None = "a9f3c2d8e471"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NODE_TYPES = ("stage", "building", "section", "floor", "room")
READ_WRITE = "SELECT, INSERT, UPDATE"
WRITERS = "kut_app, kut_worker"

INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"

# Явный список для внешнего пула: ответственные (foreman/master) и служебные created_by/updated_by
# не выдаются. Колонки, которые добавят позже, останутся закрытыми, пока их не откроют намеренно.
PORTAL_NODE_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "project_id",
    "parent_id",
    "type",
    "name",
    "code",
    "sort_order",
    "path",
    "planned_start",
    "planned_end",
    "polygon",
)

# Полигон узла: массив из 3-1000 точек [x, y]; координаты - числа в локальной системе схемы
# объекта (не географические), по модулю не больше миллиона. CASE, а не AND: SQL не гарантирует
# порядок вычисления условий, а jsonb_array_length падает на не-массиве.
IS_VALID_POLYGON = """
CREATE FUNCTION app.is_valid_polygon(p jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
    SELECT CASE
        WHEN p IS NULL THEN true
        WHEN jsonb_typeof(p) <> 'array' THEN false
        WHEN jsonb_array_length(p) NOT BETWEEN 3 AND 1000 THEN false
        ELSE NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(p) AS pt
            WHERE CASE
                WHEN jsonb_typeof(pt) <> 'array' THEN true
                WHEN jsonb_array_length(pt) <> 2 THEN true
                WHEN jsonb_typeof(pt -> 0) <> 'number' THEN true
                WHEN jsonb_typeof(pt -> 1) <> 'number' THEN true
                ELSE abs((pt ->> 0)::numeric) > 1000000 OR abs((pt ->> 1)::numeric) > 1000000
            END)
    END
$$
"""

# Матрица допустимых дочерних типов (решение PO): корень объекта - этап или корпус; этап - корпус
# или секция; корпус - секция или этаж; секция - этаж; этаж - помещение; помещение - никто.
CHILD_ALLOWED = """
CREATE FUNCTION app.project_node_child_allowed(p_parent text, p_child text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
    SELECT (COALESCE(p_parent, 'project'), p_child) IN (
        VALUES ('project', 'stage'), ('project', 'building'),
               ('stage', 'building'), ('stage', 'section'),
               ('building', 'section'), ('building', 'floor'),
               ('section', 'floor'), ('floor', 'room'))
$$
"""

# ТОЧКИ РАСШИРЕНИЯ. Узел (и объект) нельзя удалить, пока к ним привязано что-то живое
# (ТЗ §5.1). Сейчас это только живые потомки; ГПР, факт, документы и прочее добавят свои проверки
# через CREATE OR REPLACE этих функций в своих миграциях. SECURITY DEFINER от kut_auth
# (BYPASSRLS): проверка не должна зависеть от того, что видит вызывающий.
NODE_HAS_DEPENDENTS = """
CREATE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
$$
"""

PROJECT_HAS_DEPENDENTS = """
CREATE FUNCTION app.project_has_dependents(p_project uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node node
        WHERE node.project_id = p_project AND node.deleted_at IS NULL)
$$
"""

# Проверки дерева и путь. Родитель ищется в том же объекте (составной FK это же гарантирует, но
# позже и с менее понятной ошибкой). path вручную менять нельзя: исключение - вложенный UPDATE из
# триггера project_node_repath (глубина триггеров >= 2).
NODE_GUARD = """
CREATE FUNCTION app.project_node_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    v_parent_type text;
    v_parent_path uuid[];
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
           AND app.project_node_has_dependents(OLD.id) THEN
            RAISE EXCEPTION 'node.has_dependents' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id AND NEW.type = OLD.type
           AND NOT (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL) THEN
            IF NEW.path IS DISTINCT FROM OLD.path AND pg_trigger_depth() < 2 THEN
                RAISE EXCEPTION 'node.path_is_managed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END IF;
    END IF;

    IF NEW.parent_id IS NULL THEN
        v_parent_type := NULL;
        v_parent_path := ARRAY[]::uuid[];
    ELSE
        SELECT parent.type, parent.path INTO v_parent_type, v_parent_path
        FROM public.project_node parent
        WHERE parent.id = NEW.parent_id AND parent.project_id = NEW.project_id
          AND parent.deleted_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'node.parent_invalid' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.id = ANY (v_parent_path) THEN
            RAISE EXCEPTION 'node.cycle' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NOT app.project_node_child_allowed(v_parent_type, NEW.type) THEN
        RAISE EXCEPTION 'node.type_not_allowed' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.type <> OLD.type AND EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = NEW.id AND child.deleted_at IS NULL
          AND NOT app.project_node_child_allowed(NEW.type, child.type)
    ) THEN
        RAISE EXCEPTION 'node.type_not_allowed' USING ERRCODE = 'check_violation';
    END IF;
    NEW.path := v_parent_path || NEW.id;
    RETURN NEW;
END $$
"""

# Перенос узла меняет путь всего поддерева (включая мягко удалённых потомков): заменяем префикс.
NODE_REPATH = """
CREATE FUNCTION app.project_node_repath() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.project_node descendant
    SET path = NEW.path || descendant.path[cardinality(OLD.path) + 1:]
    WHERE descendant.project_id = NEW.project_id AND descendant.id <> NEW.id
      AND descendant.path @> ARRAY[NEW.id];
    RETURN NULL;
END $$
"""

PROJECT_GUARD = """
CREATE FUNCTION app.project_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND app.project_has_dependents(OLD.id) THEN
        RAISE EXCEPTION 'project.has_dependents' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""


def _business_table(name: str, *items: sa.schema.SchemaItem) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *items,
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
    )
    for column in ("created_by", "updated_by"):
        op.create_foreign_key(op.f(f"fk_{name}_{column}_user"), name, "user", [column], ["id"])


def upgrade() -> None:
    op.execute(IS_VALID_POLYGON)
    op.execute(CHILD_ALLOWED)
    for signature in ("app.is_valid_polygon(jsonb)", "app.project_node_child_allowed(text, text)"):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {WRITERS}")

    node_types = ", ".join(f"'{kind}'" for kind in NODE_TYPES)
    _business_table(
        "project_node",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "path",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("foreman_user_id", sa.Uuid(), nullable=True),
        sa.Column("master_user_id", sa.Uuid(), nullable=True),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("polygon", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_project_node_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "parent_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_project_node_project_id_project_node",
        ),
        sa.ForeignKeyConstraint(
            ["foreman_user_id"], ["user.id"], name=op.f("fk_project_node_foreman_user_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["master_user_id"], ["user.id"], name=op.f("fk_project_node_master_user_id_user")
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_project_node_project_id_id"),
        sa.CheckConstraint(f"type IN ({node_types})", name=op.f("ck_project_node_type_valid")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_project_node_name_not_blank")),
        sa.CheckConstraint(
            "code IS NULL OR btrim(code) <> ''", name=op.f("ck_project_node_code_not_blank")
        ),
        sa.CheckConstraint(
            "planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start",
            name=op.f("ck_project_node_planned_dates_ordered"),
        ),
        sa.CheckConstraint(
            "app.is_valid_polygon(polygon)", name=op.f("ck_project_node_polygon_valid")
        ),
    )
    op.create_index("ix_project_node_legal_entity_id", "project_node", ["legal_entity_id"])
    op.create_index("ix_project_node_foreman_user_id", "project_node", ["foreman_user_id"])
    op.create_index("ix_project_node_master_user_id", "project_node", ["master_user_id"])
    op.create_index(
        "ix_project_node_project_id_parent_id_sort_order",
        "project_node",
        ["project_id", "parent_id", "sort_order"],
    )
    op.create_index("ix_project_node_path", "project_node", ["path"], postgresql_using="gin")
    op.create_index(
        "uq_project_node_root_code",
        "project_node",
        ["project_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND code IS NOT NULL AND parent_id IS NULL"),
    )
    op.create_index(
        "uq_project_node_sibling_code",
        "project_node",
        ["project_id", "parent_id", "code"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND code IS NOT NULL AND parent_id IS NOT NULL"
        ),
    )

    _business_table(
        "work",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.CheckConstraint(
            "btrim(code) <> '' AND btrim(name) <> ''", name=op.f("ck_work_code_and_name_not_blank")
        ),
        sa.CheckConstraint(
            "btrim(unit) <> '' AND btrim(category) <> ''",
            name=op.f("ck_work_unit_and_category_not_blank"),
        ),
    )
    op.create_index("ix_work_category", "work", ["category"])
    op.create_index(
        "uq_work_code",
        "work",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Точки расширения: функции принадлежат kut_auth (BYPASSRLS), которому нужно читать узлы.
    op.execute("GRANT SELECT ON project_node TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    for sql, signature in (
        (NODE_HAS_DEPENDENTS, "app.project_node_has_dependents(uuid)"),
        (PROJECT_HAS_DEPENDENTS, "app.project_has_dependents(uuid)"),
    ):
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {WRITERS}")
    op.execute("RESET ROLE")

    for sql in (NODE_GUARD, NODE_REPATH, PROJECT_GUARD):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER project_node_guard BEFORE INSERT OR UPDATE ON project_node"
        " FOR EACH ROW EXECUTE FUNCTION app.project_node_guard()"
    )
    op.execute(
        "CREATE TRIGGER project_node_repath AFTER UPDATE OF parent_id ON project_node"
        " FOR EACH ROW WHEN (NEW.parent_id IS DISTINCT FROM OLD.parent_id)"
        " EXECUTE FUNCTION app.project_node_repath()"
    )
    op.execute(
        "CREATE TRIGGER project_guard BEFORE UPDATE OF deleted_at ON project"
        " FOR EACH ROW EXECUTE FUNCTION app.project_guard()"
    )

    for table in ("project_node", "work"):
        force_rls(table)
    policies = (
        (
            "project_node_select_app",
            "project_node",
            "SELECT",
            "kut_app",
            f"{INTERNAL} AND project_id = ANY {PROJECTS}",
            None,
        ),
        (
            "project_node_select_portal",
            "project_node",
            "SELECT",
            "kut_portal",
            f"project_id = ANY {PROJECTS} AND deleted_at IS NULL",
            None,
        ),
        (
            "project_node_insert_app",
            "project_node",
            "INSERT",
            "kut_app",
            None,
            f"{INTERNAL} AND project_id = ANY {PROJECTS}",
        ),
        (
            "project_node_update_app",
            "project_node",
            "UPDATE",
            "kut_app",
            f"{INTERNAL} AND project_id = ANY {PROJECTS}",
            f"{INTERNAL} AND project_id = ANY {PROJECTS}",
        ),
        ("work_select_app", "work", "SELECT", "kut_app", INTERNAL, None),
        ("work_insert_app", "work", "INSERT", "kut_app", None, INTERNAL),
        ("work_update_app", "work", "UPDATE", "kut_app", INTERNAL, INTERNAL),
    )
    for name, table, command, login, using, check in policies:
        clauses = ""
        if using:
            clauses += f" USING ({using})"
        if check:
            clauses += f" WITH CHECK ({check})"
        op.execute(f"CREATE POLICY {name} ON {table} FOR {command} TO {login}{clauses}")

    grant("project_node", app=READ_WRITE, worker=READ_WRITE, portal_columns=PORTAL_NODE_COLUMNS)
    grant("work", app=READ_WRITE, worker=READ_WRITE)

    # path скрыт из журнала: он производный, а перенос поддерева иначе записал бы строку на узел.
    create_audit_triggers(
        "project_node",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        hidden_columns=("path",),
        frozen_columns=("project_id", "legal_entity_id"),
    )
    create_audit_triggers("work")


def downgrade() -> None:
    drop_audit_triggers("work")
    drop_audit_triggers("project_node")
    for table in ("work", "project_node"):
        op.execute(f"REVOKE ALL ON {table} FROM kut_app, kut_worker, kut_portal")
    for name, table in (
        ("work_update_app", "work"),
        ("work_insert_app", "work"),
        ("work_select_app", "work"),
        ("project_node_update_app", "project_node"),
        ("project_node_insert_app", "project_node"),
        ("project_node_select_portal", "project_node"),
        ("project_node_select_app", "project_node"),
    ):
        op.execute(f"DROP POLICY {name} ON {table}")

    op.execute("DROP TRIGGER project_guard ON project")
    op.execute("DROP TRIGGER project_node_repath ON project_node")
    op.execute("DROP TRIGGER project_node_guard ON project_node")
    op.drop_table("work")
    op.drop_table("project_node")
    # Функции точек расширения принадлежат kut_auth: удалять их может только он.
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION app.project_has_dependents(uuid)")
    op.execute("DROP FUNCTION app.project_node_has_dependents(uuid)")
    op.execute("RESET ROLE")
    for signature in (
        "app.project_guard()",
        "app.project_node_repath()",
        "app.project_node_guard()",
        "app.project_node_child_allowed(text, text)",
        "app.is_valid_polygon(jsonb)",
    ):
        op.execute(f"DROP FUNCTION {signature}")
