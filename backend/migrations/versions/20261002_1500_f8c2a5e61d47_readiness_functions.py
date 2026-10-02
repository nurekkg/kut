"""readiness: % готовности работы, узла, поддерева и объекта (ТЗ §5.2, решения PO по S3)

Revision ID: f8c2a5e61d47
Revises: e1b7c4d93a86
Create Date: 2026-10-02 15:00:00.000000

Расчёт в SQL: работа = min(подтверждённый факт / плановый объём, 1); узел = сумма(вес x %) / сумма веса
по работам узла; поддерево и объект - агрегат по тем же весам (не среднее процентов детей). Вес по
умолчанию - плановая длительность в днях, schedule_item.weight переопределяет его вручную. В расчёт
идёт только факт со статусом approved. Функции работают от имени вызывающего (SECURITY INVOKER):
границу доступа ставит RLS, поэтому внешняя роль считает только по своему объекту и подтверждённому
факту.

Отклонения от плана - по линейному плану: на конец дня as_of должно быть выполнено
(as_of - plan_start + 1) / длительность строки. Отставание в днях = прошло дней - доля факта x
длительность (плюс - отстаём, минус - опережаем).

ТОЧКА РАСШИРЕНИЯ (S9, сметы): вес считает одна функция app.schedule_item_weight; после появления
смет её тело заменяют на вес по стоимости (CREATE OR REPLACE), остальной расчёт не меняется.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8c2a5e61d47"
down_revision: str | Sequence[str] | None = "e1b7c4d93a86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGINS = "kut_app, kut_worker, kut_portal"

SCHEDULE_ITEM_WEIGHT = """
CREATE FUNCTION app.schedule_item_weight(p_item uuid, p_weight numeric, p_start date, p_end date)
RETURNS numeric LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT COALESCE(p_weight, (p_end - p_start + 1)::numeric)
$$
"""

# p_root ограничивает расчёт поддеревом узла (узел и все его потомки); NULL - весь объект.
SCHEDULE_PROGRESS = """
CREATE FUNCTION app.schedule_progress(p_project uuid, p_as_of date, p_root uuid DEFAULT NULL)
RETURNS TABLE (
    item_id uuid, node_id uuid, work_id uuid, plan_start date, plan_end date,
    plan_volume numeric, weight numeric, confirmed_volume numeric, fraction numeric,
    planned_fraction numeric, delay_days numeric, state text, overdue_days integer
) LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    WITH scoped AS (
        SELECT item.id, item.node_id, item.work_id, item.plan_start, item.plan_end,
               item.plan_volume, item.weight
        FROM public.schedule_item item
        JOIN public.project_node n ON n.id = item.node_id
        WHERE item.project_id = p_project AND item.deleted_at IS NULL
          AND (p_root IS NULL OR n.path @> ARRAY[p_root])
    ),
    fact AS (
        SELECT a.schedule_item_id, sum(a.volume) AS volume
        FROM public.work_actual a
        JOIN scoped ON scoped.id = a.schedule_item_id
        WHERE a.status = 'approved' AND a.deleted_at IS NULL
        GROUP BY a.schedule_item_id
    ),
    base AS (
        SELECT s.id, s.node_id, s.work_id, s.plan_start, s.plan_end, s.plan_volume,
               app.schedule_item_weight(s.id, s.weight, s.plan_start, s.plan_end) AS weight,
               COALESCE(f.volume, 0) AS confirmed,
               s.plan_end - s.plan_start + 1 AS days,
               LEAST(GREATEST(p_as_of - s.plan_start + 1, 0), s.plan_end - s.plan_start + 1)
                   AS elapsed
        FROM scoped s LEFT JOIN fact f ON f.schedule_item_id = s.id
    )
    SELECT b.id, b.node_id, b.work_id, b.plan_start, b.plan_end, b.plan_volume, b.weight,
           b.confirmed,
           LEAST(b.confirmed / b.plan_volume, 1),
           b.elapsed::numeric / b.days,
           b.elapsed - LEAST(b.confirmed / b.plan_volume, 1) * b.days,
           CASE
               WHEN b.confirmed >= b.plan_volume THEN 'done'
               WHEN p_as_of > b.plan_end AND b.confirmed = 0 THEN 'overdue_no_fact'
               WHEN p_as_of > b.plan_end THEN 'overdue'
               WHEN b.confirmed = 0 AND p_as_of > b.plan_start THEN 'late_start'
               WHEN b.confirmed = 0 THEN 'not_started'
               ELSE 'in_progress'
           END,
           CASE WHEN b.confirmed < b.plan_volume AND p_as_of > b.plan_end
                THEN p_as_of - b.plan_end ELSE 0 END
    FROM base b
$$
"""

# Свои итоги узла (own_*) и итоги поддерева (sub_*, узел и его потомки). Поддерево собирается один
# раз через путь узла: каждый узел с работами вносит свои суммы во все узлы своего пути, поэтому
# стоимость не растёт с числом узлов (глубина дерева не больше пяти).
PROJECT_READINESS = """
CREATE FUNCTION app.project_readiness(p_project uuid, p_as_of date, p_root uuid DEFAULT NULL)
RETURNS TABLE (
    node_id uuid, parent_id uuid,
    own_items integer, own_weight numeric, own_done numeric, own_planned numeric,
    own_delay numeric, own_overdue integer, own_overdue_no_fact integer, own_completed integer,
    sub_items integer, sub_weight numeric, sub_done numeric, sub_planned numeric,
    sub_delay numeric, sub_overdue integer, sub_overdue_no_fact integer, sub_completed integer
) LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    WITH progress AS (
        SELECT * FROM app.schedule_progress(p_project, p_as_of, p_root)
    ),
    own AS (
        SELECT pr.node_id, count(*)::integer AS items, sum(pr.weight) AS weight,
               sum(pr.weight * pr.fraction) AS done,
               sum(pr.weight * pr.planned_fraction) AS planned,
               sum(pr.weight * pr.delay_days) AS delay,
               (count(*) FILTER (WHERE pr.state IN ('overdue', 'overdue_no_fact')))::integer
                   AS overdue,
               (count(*) FILTER (WHERE pr.state = 'overdue_no_fact'))::integer AS overdue_no_fact,
               (count(*) FILTER (WHERE pr.state = 'done'))::integer AS completed
        FROM progress pr
        GROUP BY pr.node_id
    ),
    rolled AS (
        SELECT anc.id AS node_id, sum(o.items)::integer AS items, sum(o.weight) AS weight,
               sum(o.done) AS done, sum(o.planned) AS planned, sum(o.delay) AS delay,
               sum(o.overdue)::integer AS overdue,
               sum(o.overdue_no_fact)::integer AS overdue_no_fact,
               sum(o.completed)::integer AS completed
        FROM own o
        JOIN public.project_node n ON n.id = o.node_id
        CROSS JOIN LATERAL unnest(n.path) AS anc(id)
        GROUP BY anc.id
    )
    SELECT n.id, n.parent_id,
           o.items, o.weight, o.done, o.planned, o.delay, o.overdue, o.overdue_no_fact,
           o.completed,
           r.items, r.weight, r.done, r.planned, r.delay, r.overdue, r.overdue_no_fact,
           r.completed
    FROM public.project_node n
    JOIN rolled r ON r.node_id = n.id
    LEFT JOIN own o ON o.node_id = n.id
    WHERE n.project_id = p_project AND n.deleted_at IS NULL
      AND (p_root IS NULL OR n.path @> ARRAY[p_root])
$$
"""

FUNCTIONS = (
    (SCHEDULE_ITEM_WEIGHT, "app.schedule_item_weight(uuid, numeric, date, date)"),
    (SCHEDULE_PROGRESS, "app.schedule_progress(uuid, date, uuid)"),
    (PROJECT_READINESS, "app.project_readiness(uuid, date, uuid)"),
)


def upgrade() -> None:
    for sql, signature in FUNCTIONS:
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {LOGINS}")


def downgrade() -> None:
    for _sql, signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
