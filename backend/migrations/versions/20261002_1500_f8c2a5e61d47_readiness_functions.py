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
#
# Планы запросов. Политики RLS добавляют к каждой таблице условие project_id = ANY(<InitPlan>),
# для которого планировщик не знает длину массива и занижает число строк до единиц. Из-за этого
# на первых вызовах он выбирал вложенный цикл, в котором агрегат фактов (или агрегат по предкам
# узла) заново считался на каждую строку внешнего набора: 4,6-19 секунд на объекте из 10 665
# узлов и 4800 работ вместо 0,1 секунды (замер: wiki/arch/s3a-evidence/perf-readiness-10k.txt).
# Поэтому здесь нет соединений с агрегатом: строки ГПР и подтверждённый факт идут одним
# запросом UNION ALL, объём факта суммирует оконная функция по строке ГПР, путь узла приходит
# вместе со строкой. Стоимость линейна и от оценок числа строк не зависит.
SCHEDULE_PROGRESS = """
CREATE FUNCTION app.schedule_progress(p_project uuid, p_as_of date, p_root uuid DEFAULT NULL)
RETURNS TABLE (
    item_id uuid, node_id uuid, work_id uuid, plan_start date, plan_end date,
    plan_volume numeric, weight numeric, confirmed_volume numeric, fraction numeric,
    planned_fraction numeric, delay_days numeric, state text, overdue_days integer,
    node_path uuid[]
) LANGUAGE sql STABLE ROWS 5000 SET search_path = pg_catalog AS $$
    SELECT b.item_id, b.node_id, b.work_id, b.plan_start, b.plan_end, b.plan_volume, b.weight,
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
                THEN p_as_of - b.plan_end ELSE 0 END,
           b.node_path
    FROM (
        SELECT w.item_id, w.node_id, w.work_id, w.plan_start, w.plan_end, w.plan_volume,
               w.node_path,
               app.schedule_item_weight(w.item_id, w.weight, w.plan_start, w.plan_end) AS weight,
               w.confirmed,
               w.plan_end - w.plan_start + 1 AS days,
               LEAST(GREATEST(p_as_of - w.plan_start + 1, 0), w.plan_end - w.plan_start + 1)
                   AS elapsed
        FROM (
            SELECT u.*, COALESCE(sum(u.volume) OVER (PARTITION BY u.item_id), 0) AS confirmed
            FROM (
                SELECT item.id AS item_id, true AS is_item, item.node_id, item.work_id,
                       item.plan_start, item.plan_end, item.plan_volume, item.weight,
                       n.path AS node_path, NULL::numeric AS volume
                FROM public.schedule_item item
                JOIN public.project_node n ON n.id = item.node_id
                WHERE item.project_id = p_project AND item.deleted_at IS NULL
                  AND (p_root IS NULL OR n.path @> ARRAY[p_root])
                UNION ALL
                SELECT a.schedule_item_id, false, NULL::uuid, NULL::uuid, NULL::date, NULL::date,
                       NULL::numeric, NULL::numeric, NULL::uuid[], a.volume
                FROM public.work_actual a
                WHERE a.project_id = p_project AND a.status = 'approved'
                  AND a.deleted_at IS NULL AND a.schedule_item_id IS NOT NULL
            ) u
        ) w
        WHERE w.is_item
    ) b
$$
"""

# Свои итоги узла (own_*) и итоги поддерева (sub_*, узел и его потомки) одним проходом: каждая
# работа вносит свои суммы во все узлы своего пути (глубина дерева не больше пяти), поэтому
# стоимость растёт с числом работ, а не с числом узлов. Свои итоги пусты (NULL) у узла без работ.
# Родитель узла берётся из того же пути (предыдущий элемент), отдельного соединения с деревом нет.
PROJECT_READINESS = """
CREATE FUNCTION app.project_readiness(p_project uuid, p_as_of date, p_root uuid DEFAULT NULL)
RETURNS TABLE (
    node_id uuid, parent_id uuid,
    own_items integer, own_weight numeric, own_done numeric, own_planned numeric,
    own_delay numeric, own_overdue integer, own_overdue_no_fact integer, own_completed integer,
    sub_items integer, sub_weight numeric, sub_done numeric, sub_planned numeric,
    sub_delay numeric, sub_overdue integer, sub_overdue_no_fact integer, sub_completed integer
) LANGUAGE sql STABLE ROWS 2000 SET search_path = pg_catalog AS $$
    SELECT r.node_id, r.parent_id,
           NULLIF(r.own_items, 0), r.own_weight, r.own_done, r.own_planned, r.own_delay,
           CASE WHEN r.own_items > 0 THEN r.own_overdue END,
           CASE WHEN r.own_items > 0 THEN r.own_overdue_no_fact END,
           CASE WHEN r.own_items > 0 THEN r.own_completed END,
           r.sub_items, r.sub_weight, r.sub_done, r.sub_planned, r.sub_delay,
           r.sub_overdue, r.sub_overdue_no_fact, r.sub_completed
    FROM (
        SELECT anc.id AS node_id,
               pr.node_path[anc.pos - 1] AS parent_id,
               (count(*) FILTER (WHERE anc.id = pr.node_id))::integer AS own_items,
               sum(pr.weight) FILTER (WHERE anc.id = pr.node_id) AS own_weight,
               sum(pr.weight * pr.fraction) FILTER (WHERE anc.id = pr.node_id) AS own_done,
               sum(pr.weight * pr.planned_fraction) FILTER (WHERE anc.id = pr.node_id)
                   AS own_planned,
               sum(pr.weight * pr.delay_days) FILTER (WHERE anc.id = pr.node_id) AS own_delay,
               (count(*) FILTER (WHERE anc.id = pr.node_id
                                 AND pr.state IN ('overdue', 'overdue_no_fact')))::integer
                   AS own_overdue,
               (count(*) FILTER (WHERE anc.id = pr.node_id AND pr.state = 'overdue_no_fact'))::integer
                   AS own_overdue_no_fact,
               (count(*) FILTER (WHERE anc.id = pr.node_id AND pr.state = 'done'))::integer
                   AS own_completed,
               count(*)::integer AS sub_items,
               sum(pr.weight) AS sub_weight,
               sum(pr.weight * pr.fraction) AS sub_done,
               sum(pr.weight * pr.planned_fraction) AS sub_planned,
               sum(pr.weight * pr.delay_days) AS sub_delay,
               (count(*) FILTER (WHERE pr.state IN ('overdue', 'overdue_no_fact')))::integer
                   AS sub_overdue,
               (count(*) FILTER (WHERE pr.state = 'overdue_no_fact'))::integer
                   AS sub_overdue_no_fact,
               (count(*) FILTER (WHERE pr.state = 'done'))::integer AS sub_completed
        FROM app.schedule_progress(p_project, p_as_of, p_root) pr
        CROSS JOIN LATERAL unnest(pr.node_path) WITH ORDINALITY AS anc(id, pos)
        WHERE p_root IS NULL OR anc.pos >= array_position(pr.node_path, p_root)
        GROUP BY anc.id, pr.node_path[anc.pos - 1]
    ) r
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
