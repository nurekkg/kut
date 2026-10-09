"""schedule_item and schedule_dependency (ГПР, ТЗ §5.2)

Revision ID: d6a3f8b52e19
Revises: c4d9e2a71f58
Create Date: 2026-10-02 14:00:00.000000

ГПР: плановая строка «узел x вид работ» с датами, объёмом и необязательным весом, плюс зависимости
«окончание - начало» (FS). Правила живут в БД, чтобы их не обошёл ни один писатель: один объект у
узла, работы и зависимости, живые узел и вид работ, запрет цикла зависимостей, запрет удалить строку
со связями. Точки расширения S2 использованы по назначению: app.project_node_has_dependents теперь
учитывает ГПР (CREATE OR REPLACE), а у справочника work появился такой же запрет (удаление и смена
единицы измерения при живых строках ГПР).

Внешний пул kut_portal читает ГПР своего объекта (явный список колонок, без ответственного и
юрлица) и ровно те виды работ, что встречаются в ГПР его объектов (справочник work закрыт для него
в остальном).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "d6a3f8b52e19"
down_revision: str | Sequence[str] | None = "c4d9e2a71f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE = "SELECT, INSERT, UPDATE"
WRITERS = "kut_app, kut_worker"
INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"

PORTAL_ITEM_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "project_id",
    "node_id",
    "work_id",
    "plan_start",
    "plan_end",
    "plan_volume",
    "weight",
)
PORTAL_DEPENDENCY_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "project_id",
    "predecessor_id",
    "successor_id",
)
PORTAL_WORK_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "code",
    "name",
    "unit",
    "category",
)

# ТОЧКИ РАСШИРЕНИЯ (см. S2): живые связи, мешающие удалению. SECURITY DEFINER от kut_auth.
SCHEDULE_ITEM_HAS_DEPENDENTS = """
CREATE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
$$
"""

WORK_HAS_DEPENDENTS = """
CREATE FUNCTION app.work_has_dependents(p_work uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.work_id = p_work AND item.deleted_at IS NULL)
$$
"""

NODE_HAS_DEPENDENTS = """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.node_id = p_node AND item.deleted_at IS NULL)
$$
"""

NODE_HAS_DEPENDENTS_S2 = """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
$$
"""

# Права на запись ГПР проверяются и в БД (gpr:write по контексту запроса): системный контекст
# (сид, воркер) проходит без пользователя. Узел и вид работ должны быть живыми при создании строки.
SCHEDULE_ITEM_GUARD = """
CREATE FUNCTION app.schedule_item_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF app.user_id() IS NOT NULL AND NOT app.has_permission('gpr', 'write') THEN
        RAISE EXCEPTION 'schedule.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL) THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.project_node n
            WHERE n.id = NEW.node_id AND n.project_id = NEW.project_id AND n.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'schedule.node_invalid' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.work w WHERE w.id = NEW.work_id AND w.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'schedule.work_invalid' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND app.schedule_item_has_dependents(OLD.id) THEN
        RAISE EXCEPTION 'schedule.has_dependents' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""

# Зависимость p -> s замыкает цикл, если p достижим из s по уже существующим зависимостям.
# Блокировка по объекту сериализует создание зависимостей: две параллельные транзакции, каждая из
# которых не видит чужую, иначе могли бы вместе замкнуть цикл. Функция не LANGUAGE sql: каждый
# оператор plpgsql берёт новый снимок и после ожидания блокировки видит чужой COMMIT.
SCHEDULE_DEPENDENCY_GUARD = """
CREATE FUNCTION app.schedule_dependency_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF app.user_id() IS NOT NULL AND NOT app.has_permission('gpr', 'write') THEN
        RAISE EXCEPTION 'schedule.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.deleted_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.predecessor_id = NEW.successor_id THEN
        RAISE EXCEPTION 'dependency.cycle' USING ERRCODE = 'check_violation';
    END IF;
    IF (SELECT count(*) FROM public.schedule_item item
        WHERE item.id IN (NEW.predecessor_id, NEW.successor_id)
          AND item.project_id = NEW.project_id AND item.deleted_at IS NULL) <> 2 THEN
        RAISE EXCEPTION 'dependency.item_invalid' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.project_id::text, 0));
    IF EXISTS (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.successor_id
            UNION
            SELECT dep.successor_id FROM public.schedule_dependency dep
            JOIN reach ON dep.predecessor_id = reach.id
            WHERE dep.deleted_at IS NULL AND dep.id <> NEW.id
        )
        SELECT 1 FROM reach WHERE reach.id = NEW.predecessor_id
    ) THEN
        RAISE EXCEPTION 'dependency.cycle' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""

# Справочник видов работ: нельзя удалить вид работ и сменить его единицу измерения, пока на него
# ссылаются живые строки (объёмы в ГПР и факте иначе поменяли бы смысл задним числом).
WORK_GUARD = """
CREATE FUNCTION app.work_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
       AND app.work_has_dependents(OLD.id) THEN
        RAISE EXCEPTION 'work.has_dependents' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.unit IS DISTINCT FROM OLD.unit AND app.work_has_dependents(OLD.id) THEN
        RAISE EXCEPTION 'work.unit_locked' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    business_table(
        "schedule_item",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("plan_start", sa.Date(), nullable=False),
        sa.Column("plan_end", sa.Date(), nullable=False),
        sa.Column("plan_volume", sa.Numeric(18, 3), nullable=False),
        sa.Column("weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("responsible_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_schedule_item_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_schedule_item_project_id_project_node",
        ),
        sa.ForeignKeyConstraint(
            ["work_id"], ["work.id"], name=op.f("fk_schedule_item_work_id_work")
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"],
            ["user.id"],
            name=op.f("fk_schedule_item_responsible_user_id_user"),
        ),
        # Цели составных внешних ключей: зависимостей (project_id, id) и факта работ
        # (project_id, id, node_id, work_id): факт, привязанный к строке ГПР, того же узла и вида работ.
        sa.UniqueConstraint("project_id", "id", name="uq_schedule_item_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            "node_id",
            "work_id",
            name="uq_schedule_item_project_id_id_node_id_work_id",
        ),
        sa.CheckConstraint("plan_end >= plan_start", name=op.f("ck_schedule_item_dates_ordered")),
        sa.CheckConstraint("plan_volume > 0", name=op.f("ck_schedule_item_volume_positive")),
        sa.CheckConstraint(
            "weight IS NULL OR weight > 0", name=op.f("ck_schedule_item_weight_positive")
        ),
    )
    op.create_index(
        "uq_schedule_item_node_id_work_id",
        "schedule_item",
        ["node_id", "work_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_schedule_item_project_id_node_id", "schedule_item", ["project_id", "node_id"]
    )
    op.create_index("ix_schedule_item_work_id", "schedule_item", ["work_id"])
    op.create_index("ix_schedule_item_legal_entity_id", "schedule_item", ["legal_entity_id"])
    op.create_index(
        "ix_schedule_item_responsible_user_id", "schedule_item", ["responsible_user_id"]
    )

    business_table(
        "schedule_dependency",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("predecessor_id", sa.Uuid(), nullable=False),
        sa.Column("successor_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_schedule_dependency_project_id_project",
        ),
        # Обе строки ГПР из того же объекта (составные ключи на (project_id, id)).
        sa.ForeignKeyConstraint(
            ["project_id", "predecessor_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name="fk_schedule_dependency_predecessor_id_schedule_item",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "successor_id"],
            ["schedule_item.project_id", "schedule_item.id"],
            name="fk_schedule_dependency_successor_id_schedule_item",
        ),
        sa.CheckConstraint(
            "predecessor_id <> successor_id", name=op.f("ck_schedule_dependency_not_self")
        ),
    )
    op.create_index(
        "uq_schedule_dependency_predecessor_id_successor_id",
        "schedule_dependency",
        ["predecessor_id", "successor_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_schedule_dependency_project_id", "schedule_dependency", ["project_id"])
    op.create_index("ix_schedule_dependency_successor_id", "schedule_dependency", ["successor_id"])
    op.create_index(
        "ix_schedule_dependency_legal_entity_id", "schedule_dependency", ["legal_entity_id"]
    )

    op.execute("GRANT SELECT ON schedule_item, schedule_dependency TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    for sql, signature in (
        (SCHEDULE_ITEM_HAS_DEPENDENTS, "app.schedule_item_has_dependents(uuid)"),
        (WORK_HAS_DEPENDENTS, "app.work_has_dependents(uuid)"),
    ):
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {WRITERS}")
    op.execute(NODE_HAS_DEPENDENTS)
    op.execute("RESET ROLE")

    for sql in (SCHEDULE_ITEM_GUARD, SCHEDULE_DEPENDENCY_GUARD, WORK_GUARD):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER schedule_item_guard BEFORE INSERT OR UPDATE ON schedule_item"
        " FOR EACH ROW EXECUTE FUNCTION app.schedule_item_guard()"
    )
    op.execute(
        "CREATE TRIGGER schedule_dependency_guard"
        " BEFORE INSERT OR UPDATE OF predecessor_id, successor_id, deleted_at ON schedule_dependency"
        " FOR EACH ROW EXECUTE FUNCTION app.schedule_dependency_guard()"
    )
    op.execute(
        "CREATE TRIGGER work_guard BEFORE UPDATE OF unit, deleted_at ON work"
        " FOR EACH ROW EXECUTE FUNCTION app.work_guard()"
    )

    for table in ("schedule_item", "schedule_dependency"):
        force_rls(table)
        op.execute(
            f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app"
            f" USING ({INTERNAL} AND project_id = ANY {PROJECTS})"
        )
        op.execute(
            f"CREATE POLICY {table}_select_portal ON {table} FOR SELECT TO kut_portal"
            f" USING (project_id = ANY {PROJECTS} AND deleted_at IS NULL)"
        )
        op.execute(
            f"CREATE POLICY {table}_insert_app ON {table} FOR INSERT TO kut_app"
            f" WITH CHECK ({INTERNAL} AND project_id = ANY {PROJECTS})"
        )
        op.execute(
            f"CREATE POLICY {table}_update_app ON {table} FOR UPDATE TO kut_app"
            f" USING ({INTERNAL} AND project_id = ANY {PROJECTS})"
            f" WITH CHECK ({INTERNAL} AND project_id = ANY {PROJECTS})"
        )
    grant("schedule_item", app=READ_WRITE, worker=READ_WRITE, portal_columns=PORTAL_ITEM_COLUMNS)
    grant(
        "schedule_dependency",
        app=READ_WRITE,
        worker=READ_WRITE,
        portal_columns=PORTAL_DEPENDENCY_COLUMNS,
    )

    # Внешним ролям нужны названия работ из ГПР своего объекта: справочник открыт им ровно в части
    # видов работ, которые встречаются в ГПР их объектов, и только выданными колонками.
    op.execute(
        "CREATE POLICY work_select_portal ON work FOR SELECT TO kut_portal USING ("
        "deleted_at IS NULL AND EXISTS (SELECT 1 FROM schedule_item item"
        " WHERE item.work_id = work.id AND item.deleted_at IS NULL"
        f" AND item.project_id = ANY {PROJECTS}))"
    )
    grant("work", portal_columns=PORTAL_WORK_COLUMNS)

    create_audit_triggers(
        "schedule_item",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id", "node_id", "work_id"),
    )
    create_audit_triggers(
        "schedule_dependency",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id", "predecessor_id", "successor_id"),
    )


def downgrade() -> None:
    op.execute("REVOKE ALL ON work FROM kut_portal")
    op.execute("DROP POLICY work_select_portal ON work")
    op.execute("DROP TRIGGER work_guard ON work")
    op.execute("DROP FUNCTION app.work_guard()")

    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(NODE_HAS_DEPENDENTS_S2)
    op.execute("RESET ROLE")

    op.drop_table("schedule_dependency")
    op.drop_table("schedule_item")
    op.execute("DROP FUNCTION app.schedule_dependency_guard()")
    op.execute("DROP FUNCTION app.schedule_item_guard()")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION app.work_has_dependents(uuid)")
    op.execute("DROP FUNCTION app.schedule_item_has_dependents(uuid)")
    op.execute("RESET ROLE")
