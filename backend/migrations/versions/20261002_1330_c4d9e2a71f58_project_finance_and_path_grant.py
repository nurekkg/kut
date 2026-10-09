"""project_finance (budget, customer, address, warranty by finance:read) and UPDATE(path) closed

Revision ID: c4d9e2a71f58
Revises: b5e8d1a7c963
Create Date: 2026-10-02 13:30:00.000000

Хвосты S2 по решениям PO. Миграции до b5e8d1a7c963 включительно заморожены: всё здесь - новая
ревизия поверх них.

1. Бюджет, заказчик, адрес, координаты и гарантия объекта видят только роли с правом finance:read
   (ТЗ §7.3, модуль «Финансы»). Эти поля переезжают из project в project_finance: строки закрыты
   политикой RLS по праву (app.has_permission), а не только условием в API. Внешний пул kut_portal
   к таблице не допущен совсем.
2. kut_app и kut_worker больше не пишут колонку project_node.path: путь ведёт БД. Функция
   переписывания пути при переносе поддерева стала SECURITY DEFINER от kut_auth; триггер
   project_node_guard остаётся второй линией защиты.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "c4d9e2a71f58"
down_revision: str | Sequence[str] | None = "b5e8d1a7c963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE = "SELECT, INSERT, UPDATE"
WRITERS = "kut_app, kut_worker"
INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
# InitPlan: функция вычисляется один раз на запрос, а не на каждую строку.
CAN_SEE_FINANCE = "(SELECT app.has_permission('finance', 'read'))"
CAN_EDIT_CARD = "(SELECT app.has_permission('objects', 'write'))"

FINANCE_COLUMNS = (
    "planned_budget",
    "budget_currency",
    "customer_id",
    "address",
    "latitude",
    "longitude",
    "warranty_until",
)
FINANCE_COLUMN_LIST = ", ".join(FINANCE_COLUMNS)

# Все колонки project_node, кроме path: путь пишет только БД (definer-функция переписывания).
NODE_UPDATABLE = (
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "deleted_at",
    "project_id",
    "legal_entity_id",
    "parent_id",
    "type",
    "name",
    "code",
    "sort_order",
    "foreman_user_id",
    "master_user_id",
    "planned_start",
    "planned_end",
    "polygon",
)
NODE_UPDATABLE_LIST = ", ".join(NODE_UPDATABLE)

# Право по контексту запроса (app.user_id()), как app.require_settings_admin(): параметром пользователя
# не называют. Без пользователя (системный контекст) права нет: вызывающий проверяет это сам.
HAS_PERMISSION = """
CREATE FUNCTION app.has_permission(p_module text, p_action text, p_qualifier text DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public."user" usr
        JOIN public.role_permission rp ON rp.role_id = usr.role_id AND rp.deleted_at IS NULL
        JOIN public.permission p ON p.id = rp.permission_id AND p.deleted_at IS NULL
        WHERE usr.id = app.user_id() AND usr.is_active AND usr.deleted_at IS NULL
          AND p.module = p_module AND p.action = p_action
          AND p.qualifier IS NOT DISTINCT FROM p_qualifier)
$$
"""

# Перенос поддерева меняет путь всех потомков (включая мягко удалённых): заменяем префикс. Функция
# SECURITY DEFINER, потому что у логинов приложения права на колонку path больше нет. Границу
# доступа ставит вызывающий UPDATE (политика RLS на перенесённый узел): потомки того же объекта.
NODE_REPATH_DEFINER = """
CREATE FUNCTION app.project_node_repath() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.project_node descendant
    SET path = NEW.path || descendant.path[cardinality(OLD.path) + 1:]
    WHERE descendant.project_id = NEW.project_id AND descendant.id <> NEW.id
      AND descendant.path @> ARRAY[NEW.id];
    RETURN NULL;
END $$
"""

# Версия S2 (SECURITY INVOKER) для downgrade.
NODE_REPATH_INVOKER = """
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

REPATH_TRIGGER = (
    "CREATE TRIGGER project_node_repath AFTER UPDATE OF parent_id ON project_node"
    " FOR EACH ROW WHEN (NEW.parent_id IS DISTINCT FROM OLD.parent_id)"
    " EXECUTE FUNCTION app.project_node_repath()"
)


def _create_repath(sql: str, *, definer: bool) -> None:
    op.execute("DROP TRIGGER project_node_repath ON project_node")
    op.execute("DROP FUNCTION app.project_node_repath()")
    if definer:
        op.execute("SET LOCAL ROLE kut_auth")
    op.execute(sql)
    if definer:
        # Триггер создаёт kut_owner: ему нужно право выполнения (владелец функции - kut_auth).
        op.execute("GRANT EXECUTE ON FUNCTION app.project_node_repath() TO kut_owner")
        op.execute("RESET ROLE")
    op.execute(REPATH_TRIGGER)


def upgrade() -> None:
    # --- право по контексту запроса ---
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(HAS_PERMISSION)
    op.execute("REVOKE ALL ON FUNCTION app.has_permission(text, text, text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.has_permission(text, text, text) TO {WRITERS}")
    op.execute("RESET ROLE")

    # --- project_finance ---
    business_table(
        "project_finance",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("planned_budget", sa.Numeric(18, 2), nullable=True),
        sa.Column("budget_currency", sa.String(), server_default="KGS", nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_project_finance_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["counterparty.id"],
            name=op.f("fk_project_finance_customer_id_counterparty"),
        ),
        sa.UniqueConstraint("project_id", name="uq_project_finance_project_id"),
        sa.CheckConstraint(
            "planned_budget IS NULL OR planned_budget >= 0",
            name=op.f("ck_project_finance_budget_not_negative"),
        ),
        sa.CheckConstraint(
            "budget_currency = 'KGS'", name=op.f("ck_project_finance_budget_currency_kgs")
        ),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name=op.f("ck_project_finance_coordinates_valid"),
        ),
    )
    op.create_index("ix_project_finance_legal_entity_id", "project_finance", ["legal_entity_id"])
    op.create_index("ix_project_finance_customer_id", "project_finance", ["customer_id"])

    # Данные переезжают до включения RLS и аудита (иначе миграция писала бы в журнал). Читать
    # project под FORCE RLS может только BYPASSRLS-роль: kut_auth, ей на время копирования
    # выдаётся INSERT.
    op.execute("GRANT INSERT ON project_finance TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(
        "INSERT INTO public.project_finance (project_id, legal_entity_id, "
        f"{FINANCE_COLUMN_LIST}) SELECT id, legal_entity_id, {FINANCE_COLUMN_LIST} "
        "FROM public.project WHERE planned_budget IS NOT NULL OR customer_id IS NOT NULL "
        "OR address IS NOT NULL OR latitude IS NOT NULL OR longitude IS NOT NULL "
        "OR warranty_until IS NOT NULL"
    )
    op.execute("RESET ROLE")
    op.execute("REVOKE INSERT ON project_finance FROM kut_auth")

    # Правило «заказчик - контрагент типа customer» (функция из a9f3c2d8e471) теперь на новой таблице.
    op.execute("DROP TRIGGER project_customer_guard ON project")
    op.execute(
        "CREATE TRIGGER project_finance_customer_guard BEFORE INSERT OR UPDATE OF customer_id"
        " ON project_finance FOR EACH ROW WHEN (NEW.customer_id IS NOT NULL)"
        " EXECUTE FUNCTION app.project_customer_guard()"
    )

    for name in (
        "ck_project_coordinates_valid",
        "ck_project_budget_currency_kgs",
        "ck_project_budget_not_negative",
    ):
        op.drop_constraint(op.f(name), "project", type_="check")
    op.drop_constraint(op.f("fk_project_customer_id_counterparty"), "project", type_="foreignkey")
    op.drop_index("ix_project_customer_id", table_name="project")
    for column in FINANCE_COLUMNS:
        op.drop_column("project", column)

    force_rls("project_finance")
    visible = f"{INTERNAL} AND project_id = ANY {PROJECTS} AND {CAN_SEE_FINANCE}"
    editable = f"{visible} AND {CAN_EDIT_CARD}"
    op.execute(
        f"CREATE POLICY project_finance_select_app ON project_finance FOR SELECT TO kut_app"
        f" USING ({visible})"
    )
    op.execute(
        f"CREATE POLICY project_finance_insert_app ON project_finance FOR INSERT TO kut_app"
        f" WITH CHECK ({editable})"
    )
    op.execute(
        f"CREATE POLICY project_finance_update_app ON project_finance FOR UPDATE TO kut_app"
        f" USING ({editable}) WITH CHECK ({editable})"
    )
    grant("project_finance", app=READ_WRITE, worker=READ_WRITE)
    create_audit_triggers(
        "project_finance",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id"),
    )
    # Журнал хранит старые и новые значения, поэтому бюджет и адрес читал бы из него любой внутренний
    # пользователь с объектом (политика журнала S1 смотрит только на объект). Записи таблицы
    # project_finance видят лишь роли с finance:read: RESTRICTIVE-политика действует поверх прежней.
    op.execute(
        "CREATE POLICY audit_log_finance_rows ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        f" USING (table_name IS DISTINCT FROM 'project_finance' OR {CAN_SEE_FINANCE})"
    )

    # --- UPDATE(path) закрыт для логинов приложения ---
    op.execute(f"REVOKE UPDATE ON project_node FROM {WRITERS}")
    op.execute(f"GRANT UPDATE ({NODE_UPDATABLE_LIST}) ON project_node TO {WRITERS}")
    op.execute("GRANT UPDATE (path) ON project_node TO kut_auth")
    # Переписывая путь потомков, kut_auth обновляет строки project_node, а CHECK полигона вызывает эту
    # функцию от его имени: без права выполнения перенос поддерева падает на «permission denied».
    op.execute("GRANT EXECUTE ON FUNCTION app.is_valid_polygon(jsonb) TO kut_auth")
    _create_repath(NODE_REPATH_DEFINER, definer=True)


def downgrade() -> None:
    op.execute("DROP POLICY audit_log_finance_rows ON audit_log")
    _create_repath(NODE_REPATH_INVOKER, definer=False)
    op.execute("REVOKE EXECUTE ON FUNCTION app.is_valid_polygon(jsonb) FROM kut_auth")
    op.execute("REVOKE UPDATE (path) ON project_node FROM kut_auth")
    op.execute(f"REVOKE UPDATE ({NODE_UPDATABLE_LIST}) ON project_node FROM {WRITERS}")
    op.execute(f"GRANT UPDATE ON project_node TO {WRITERS}")

    # Колонки возвращаются в project, данные копируются обратно (читать project_finance под
    # FORCE RLS и писать project может только kut_auth: ему права выдаются на время копирования).
    op.add_column("project", sa.Column("planned_budget", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "project", sa.Column("budget_currency", sa.String(), server_default="KGS", nullable=False)
    )
    op.add_column("project", sa.Column("customer_id", sa.Uuid(), nullable=True))
    op.add_column("project", sa.Column("address", sa.String(), nullable=True))
    op.add_column("project", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("project", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("project", sa.Column("warranty_until", sa.Date(), nullable=True))
    op.execute("GRANT SELECT ON project_finance TO kut_auth")
    op.execute(f"GRANT UPDATE ({FINANCE_COLUMN_LIST}) ON project TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    assignments = ", ".join(f"{column} = f.{column}" for column in FINANCE_COLUMNS)
    op.execute(
        f"UPDATE public.project SET {assignments} FROM public.project_finance f"
        " WHERE f.project_id = project.id"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE UPDATE ({FINANCE_COLUMN_LIST}) ON project FROM kut_auth")

    op.create_check_constraint(
        op.f("ck_project_budget_not_negative"),
        "project",
        "planned_budget IS NULL OR planned_budget >= 0",
    )
    op.create_check_constraint(
        op.f("ck_project_budget_currency_kgs"), "project", "budget_currency = 'KGS'"
    )
    op.create_check_constraint(
        op.f("ck_project_coordinates_valid"),
        "project",
        "(latitude IS NULL) = (longitude IS NULL)"
        " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))",
    )
    op.create_foreign_key(
        op.f("fk_project_customer_id_counterparty"),
        "project",
        "counterparty",
        ["customer_id"],
        ["id"],
    )
    op.create_index("ix_project_customer_id", "project", ["customer_id"], unique=False)
    op.execute(
        "CREATE TRIGGER project_customer_guard BEFORE INSERT OR UPDATE OF customer_id ON project"
        " FOR EACH ROW WHEN (NEW.customer_id IS NOT NULL)"
        " EXECUTE FUNCTION app.project_customer_guard()"
    )
    op.drop_table("project_finance")

    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION app.has_permission(text, text, text)")
    op.execute("RESET ROLE")
