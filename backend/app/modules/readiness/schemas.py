import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class Aggregate(BaseModel):
    """Итог по набору работ. percent - готовность (0-100) со взвешиванием; planned_percent - что
    должно быть готово на as_of по линейному плану; deviation = percent - planned_percent (минус -
    отставание, п.п.); delay_days - средневзвешенное отставание в днях (плюс - отстаём, минус -
    опережаем)."""

    items: int
    weight: Decimal
    percent: Decimal
    planned_percent: Decimal
    deviation: Decimal
    delay_days: Decimal
    completed: int
    overdue: int
    overdue_no_fact: int


class NodeReadiness(BaseModel):
    """own - работы самого узла (None, если их нет); subtree - узел и все его потомки."""

    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    own: Aggregate | None
    subtree: Aggregate


class ProjectReadiness(BaseModel):
    """Готовность объекта и узлов, у которых в поддереве есть ГПР (остальных в списке нет)."""

    as_of: date
    project: Aggregate | None
    nodes: list[NodeReadiness]


class ItemProgress(BaseModel):
    """Одна работа узла. state: done, in_progress, not_started, late_start (должна была начаться,
    факта нет), overdue (срок вышел, не завершена), overdue_no_fact (срок вышел, факта нет)."""

    item_id: uuid.UUID
    node_id: uuid.UUID
    work_id: uuid.UUID
    work_code: str | None
    work_name: str | None
    unit: str | None
    plan_start: date
    plan_end: date
    plan_volume: Decimal
    weight: Decimal
    confirmed_volume: Decimal
    overrun_volume: Decimal
    percent: Decimal
    planned_percent: Decimal
    delay_days: Decimal
    state: str
    overdue_days: int


class ItemProgressBrief(BaseModel):
    """Готовность одной строки ГПР в списке ГПР (S3b): те же числа, что в ItemProgress, и календарь
    подтверждённого факта (первая и последняя дата) для полосы «факт» на диаграмме. weight -
    действующий вес: ручной или плановая длительность."""

    weight: Decimal
    confirmed_volume: Decimal
    percent: Decimal
    planned_percent: Decimal
    delay_days: Decimal
    state: str
    overdue_days: int
    first_fact_date: date | None = None
    last_fact_date: date | None = None


class UnplannedVolume(BaseModel):
    """Подтверждённый факт по виду работ, которого нет в ГПР узла: в готовность он не входит."""

    work_id: uuid.UUID
    work_name: str | None
    unit: str | None
    volume: Decimal
    facts: int


class NodeReadinessDetail(BaseModel):
    as_of: date
    node_id: uuid.UUID
    own: Aggregate | None
    subtree: Aggregate | None
    children: list[NodeReadiness]
    items: list[ItemProgress]
    unplanned: list[UnplannedVolume]
