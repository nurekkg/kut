"""Правила, которые проверяет БД, в виде понятных ответов API.

Триггеры дерева объекта поднимают check_violation с машинным кодом в тексте ('node.cycle'), а
индексы и ограничения называются в журнале ошибок: здесь они превращаются в код ответа и статус.
Сервис обращается сюда при flush/commit, чтобы клиент получил не 500, а причину.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

# Код из RAISE EXCEPTION триггеров -> (статус, код ответа).
TRIGGER_RULES: dict[str, tuple[int, str]] = {
    "node.type_not_allowed": (422, "node_type_not_allowed"),
    "node.cycle": (422, "node_cycle"),
    "node.parent_invalid": (422, "node_parent_invalid"),
    "node.has_dependents": (409, "node_has_dependents"),
    "project.has_dependents": (409, "project_has_nodes"),
    "project.customer_invalid": (422, "customer_invalid"),
    # ГПР и справочник работ. Отказ по праву (forbidden) - это тот же 403, что даёт require():
    # правило в БД - вторая линия, если путь в обход API нашёл бы код без проверки.
    "schedule.forbidden": (403, "forbidden"),
    "schedule.node_invalid": (422, "schedule_node_invalid"),
    "schedule.work_invalid": (422, "schedule_work_invalid"),
    "schedule.has_dependents": (409, "schedule_item_has_dependents"),
    "dependency.cycle": (422, "dependency_cycle"),
    "dependency.item_invalid": (422, "dependency_item_invalid"),
    "work.has_dependents": (409, "work_has_dependents"),
    "work.unit_locked": (409, "work_unit_locked"),
    # Факт работ и фото.
    "actual.forbidden": (403, "forbidden"),
    "actual.transition_forbidden": (409, "actual_transition_forbidden"),
    "actual.locked": (409, "actual_locked"),
    "actual.date_in_future": (422, "actual_date_in_future"),
    "actual.reason_required": (422, "actual_reason_required"),
    "actual.node_invalid": (422, "actual_node_invalid"),
    "actual.work_invalid": (422, "actual_work_invalid"),
    "actual.item_invalid": (422, "actual_item_invalid"),
    "actual.has_dependents": (409, "actual_has_photos"),
    "photo.forbidden": (403, "forbidden"),
    # Техника (ADR-014): коды с точкой - как в ADR; триггеры правил часть 1 §1 и §3.
    "equipment.day_hours_exceeded": (409, "equipment.day_hours_exceeded"),
    "equipment.maintenance_overlap": (409, "equipment.maintenance_overlap"),
    "equipment.document_expired": (409, "equipment.document_expired"),
    "equipment.contract_not_active": (409, "equipment.contract_not_active"),
    "equipment.contract_kind_invalid": (422, "equipment.contract_kind_invalid"),
    "equipment.contract_legal_entity_mismatch": (
        422,
        "equipment.contract_legal_entity_mismatch",
    ),
    "equipment.rate_already_approved": (409, "equipment.rate_already_approved"),
}

# Имя нарушенного ограничения или индекса -> (статус, код ответа).
CONSTRAINT_RULES: dict[str, tuple[int, str]] = {
    "uq_project_node_root_code": (409, "node_code_taken"),
    "uq_project_node_sibling_code": (409, "node_code_taken"),
    "uq_work_code": (409, "work_code_taken"),
    "ck_project_node_polygon_valid": (422, "polygon_invalid"),
    "ck_project_node_planned_dates_ordered": (422, "dates_invalid"),
    "ck_project_planned_dates_ordered": (422, "dates_invalid"),
    "ck_project_finance_coordinates_valid": (422, "coordinates_invalid"),
    "ck_project_finance_budget_not_negative": (422, "budget_invalid"),
    "uq_schedule_item_node_id_work_id": (409, "schedule_item_exists"),
    "uq_schedule_dependency_predecessor_id_successor_id": (409, "dependency_exists"),
    "ck_schedule_item_dates_ordered": (422, "dates_invalid"),
    "ck_schedule_item_dates_in_range": (422, "schedule_dates_out_of_range"),
    "ck_schedule_item_duration_max": (422, "schedule_dates_out_of_range"),
    "ck_work_actual_coordinates_valid": (422, "coordinates_invalid"),
    "ck_work_actual_accuracy_valid": (422, "coordinates_invalid"),
    "ck_work_photo_coordinates_valid": (422, "coordinates_invalid"),
    "uq_work_photo_s3_key": (409, "photo_already_confirmed"),
    # Два первых одновременных сохранения коммерческой части карточки: второе повторяют.
    "uq_project_finance_project_id": (409, "finance_card_conflict"),
    # Факт указывает на строку ГПР другого узла или другого вида работ (составной внешний ключ).
    "fk_work_actual_project_id_schedule_item": (422, "actual_item_invalid"),
    # Техника (ADR-014 часть 1 §1): уникальность и проверки полей, составные внешние ключи объекта.
    "uq_equipment_type_code": (409, "equipment.type_code_taken"),
    "uq_contract_legal_entity_id_kind_number": (409, "equipment.contract_number_taken"),
    "uq_equipment_unit_legal_entity_id_registry_key": (409, "equipment.unit_key_taken"),
    "uq_equipment_own_rate_version": (409, "equipment.rate_version_exists"),
    "uq_equipment_rate_version": (409, "equipment.rate_version_exists"),
    "uq_equipment_operator_user_id": (409, "equipment.operator_user_taken"),
    "uq_equipment_document_equipment_unit_id_kind_version": (
        409,
        "equipment.document_version_conflict",
    ),
    "ck_equipment_own_rate_approver_is_not_proposer": (409, "equipment.approver_is_proposer"),
    "ck_equipment_request_reject_reason_required": (422, "equipment.reason_required"),
    "ck_equipment_assignment_cancel_reason_required": (422, "equipment.reason_required"),
    "ck_equipment_request_hours_in_day": (422, "equipment.hours_invalid"),
    "ck_equipment_assignment_hours_in_day": (422, "equipment.hours_invalid"),
    "ck_equipment_request_dates_in_range": (422, "equipment.dates_out_of_range"),
    "ck_equipment_assignment_dates_in_range": (422, "equipment.dates_out_of_range"),
    "ck_equipment_request_dates_ordered": (422, "equipment.dates_invalid"),
    "ck_equipment_assignment_dates_ordered": (422, "equipment.dates_invalid"),
    "ck_equipment_maintenance_dates_ordered": (422, "equipment.dates_invalid"),
    "ck_contract_dates_ordered": (422, "equipment.dates_invalid"),
    "ck_equipment_unit_registry_not_blank": (422, "equipment.registry_number_invalid"),
    "ck_equipment_unit_contract_matches_ownership": (
        422,
        "equipment.contract_matches_ownership",
    ),
    "fk_equipment_request_project_id_project_node": (422, "equipment.node_invalid"),
    "fk_equipment_assignment_project_id_project_node": (422, "equipment.node_invalid"),
    "fk_equipment_request_project_id_schedule_item": (422, "equipment.schedule_item_invalid"),
    "fk_equipment_assignment_project_id_schedule_item": (422, "equipment.schedule_item_invalid"),
    "fk_equipment_assignment_project_id_equipment_request": (422, "equipment.request_invalid"),
    "fk_equipment_request_equipment_type_id_equipment_type": (422, "equipment.type_invalid"),
    "fk_equipment_unit_equipment_type_id_equipment_type": (422, "equipment.type_invalid"),
    "fk_equipment_unit_contract_id_contract": (422, "equipment.contract_invalid"),
    "fk_equipment_assignment_operator_id_equipment_operator": (422, "equipment.operator_invalid"),
    "fk_equipment_maintenance_project_id_project": (422, "equipment.project_invalid"),
}


def rule_error(error: DBAPIError) -> HTTPException | None:
    """HTTP-ошибка по нарушенному правилу БД или None, если это не известное правило."""
    diag = getattr(error.orig, "diag", None)
    if diag is None:
        return None
    message = getattr(diag, "message_primary", None) or ""
    if message in TRIGGER_RULES:
        status, code = TRIGGER_RULES[message]
        return HTTPException(status, code)
    constraint = getattr(diag, "constraint_name", None) or ""
    if constraint in CONSTRAINT_RULES:
        status, code = CONSTRAINT_RULES[constraint]
        return HTTPException(status, code)
    return None


@asynccontextmanager
async def db_rules(session: AsyncSession) -> AsyncIterator[None]:
    """Нарушение известного правила БД внутри блока -> откат и HTTPException с кодом причины."""
    try:
        yield
    except DBAPIError as error:
        translated = rule_error(error)
        if translated is None:
            raise
        await session.rollback()
        raise translated from error
