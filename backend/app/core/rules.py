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
    "equipment.contract_inactive": (409, "equipment.contract_inactive"),
    # Номер и контрагент договора после активации: сообщение триггера forbid_column_change.
    "contract.counterparty_id is immutable": (409, "equipment.contract_frozen"),
    "contract.number is immutable": (409, "equipment.contract_frozen"),
    # Путевые листы (SE-2a, ADR-014 часть 1 §3 и §4): переходы, права, суточный лимит, тарифы.
    "shift.forbidden": (403, "forbidden"),
    "shift.transition_forbidden": (409, "equipment.shift_transition_forbidden"),
    "shift.locked": (409, "equipment.shift_locked"),
    "shift.not_found": (404, "not_found"),
    "shift.date_in_future": (422, "equipment.shift_date_in_future"),
    "shift.unit_invalid": (422, "equipment.unit_invalid"),
    "shift.operator_invalid": (422, "equipment.operator_invalid"),
    "shift.assignment_invalid": (422, "equipment.assignment_invalid"),
    "shift.line_invalid": (422, "equipment.shift_line_invalid"),
    "shift.day_hours_exceeded": (422, "equipment.day_hours_exceeded"),
    "shift.quantity_missing": (422, "equipment.shift_quantity_missing"),
    "shift.lines_mismatch": (422, "equipment.shift_lines_mismatch"),
    "shift.rate_missing": (422, "equipment.rate_missing"),
    "shift.rate_ambiguous": (422, "equipment.rate_ambiguous"),
    "shift.rate_unit_invalid": (422, "equipment.rate_unit_invalid"),
    # ГСМ, ремонт и журнал затрат.
    "fuel.forbidden": (403, "forbidden"),
    "fuel.not_found": (404, "not_found"),
    "fuel.unit_invalid": (422, "equipment.unit_invalid"),
    "fuel.date_in_future": (422, "equipment.shift_date_in_future"),
    "fuel.reason_required": (422, "equipment.reason_required"),
    "delegate.not_eligible": (422, "equipment.delegate_not_eligible"),
    "maintenance.forbidden": (403, "forbidden"),
    "maintenance.not_found": (404, "not_found"),
    "maintenance.transition_forbidden": (409, "equipment.transition_forbidden"),
    "maintenance.close_forbidden": (409, "equipment.maintenance_close_forbidden"),
    "maintenance.amount_required": (422, "equipment.repair_amount_required"),
    "maintenance.amount_invalid": (422, "equipment.amount_invalid"),
    "maintenance.project_invalid": (422, "equipment.project_invalid"),
    "cost_entry.immutable": (409, "equipment.cost_immutable"),
    # SE-2b: тариф по уровню, ГСМ в листе, версии листа, объект в определителях, делегаты, удаление.
    "rate.target_invalid": (422, "equipment.rate_target_invalid"),
    "rate.level_conflict": (422, "equipment.rate_level_conflict"),
    "shift.posting_forbidden": (403, "forbidden"),
    "shift.approver_is_operator": (403, "equipment.approver_is_operator"),
    "fuel.shift_bound": (409, "equipment.fuel_shift_bound"),
    "fuel.shift_invalid": (422, "equipment.shift_invalid"),
    "delegate.forbidden": (403, "forbidden"),
    "delegate.in_past": (422, "equipment.delegate_in_past"),
    "project.has_shifts": (409, "project_has_shifts"),
    # Решения PO 2026-10-13: тариф с проводками, чек заправки, накладная без объекта, ремонт в done.
    "rate.has_postings": (409, "equipment.rate_has_postings"),
    "receipt.locked": (409, "equipment.receipt_locked"),
    "receipt.invalid": (422, "equipment.receipt_invalid"),
    # Документы (ADR-015 часть 1): правила БД, которые проверяет функция или триггер.
    "document.forbidden": (403, "forbidden"),
    "document.not_found": (404, "not_found"),
    "document.transition_forbidden": (403, "forbidden"),
    "document.transition_invalid": (409, "document.transition_invalid"),
    "document.comment_required": (422, "document.comment_required"),
    "document.contract_invalid": (422, "document.contract_invalid"),
    "document.version_invalid": (422, "document.version_invalid"),
    "document.version_status_invalid": (422, "document.version_status_invalid"),
    "document.link_invalid": (422, "document.link_invalid"),
    "document.amount_not_financial": (422, "document.amount_not_financial"),
    # Сумма версии (PO 2026-10-17, R1): правка только у черновика, удаление запрещено.
    "amount.version_not_draft": (409, "document.version_not_draft"),
    "amount.delete_forbidden": (409, "document.amount_delete_forbidden"),
    "document.event_immutable": (409, "document.event_immutable"),
    # Позиции (ADR-015 часть 4): запись и удаление только в черновике, по виду и праву.
    "line.version_not_draft": (409, "document.lines_not_draft"),
    "line.delete_forbidden": (409, "document.line_delete_forbidden"),
    "line.kind_not_allowed": (422, "document.lines_not_allowed"),
    "line.deleted": (409, "document.line_deleted"),
    "line.node_invalid": (422, "document.line_invalid"),
    "line.priced_delete_forbidden": (403, "line.priced_delete_forbidden"),
    "line.quantity_invalid": (422, "document.line_quantity_invalid"),
    "document.archived": (409, "document.archived"),
    # Маршруты согласования (S6a-2, ADR-015 часть 5): решения, шаги, замещение, шаблоны.
    "approval.forbidden": (403, "forbidden"),
    "approval.not_current_step": (403, "approval.not_current_step"),
    "approval.not_your_step": (403, "approval.not_your_step"),
    "approval.author_forbidden": (403, "approval.author_forbidden"),
    "approval.version_not_on_approval": (409, "approval.version_not_on_approval"),
    "approval.version_not_draft": (409, "approval.version_not_draft"),
    "approval.reason_required": (422, "approval.reason_required"),
    "approval.decision_invalid": (422, "approval.decision_invalid"),
    "approval.template_empty": (422, "approval.template_empty"),
    "approval.template_invalid": (422, "approval.template_invalid"),
    "approval.template_not_found": (404, "not_found"),
    "approval.role_not_allowed": (422, "approval.role_not_allowed"),
    "approval.immutable": (409, "approval.immutable"),
    "approval.delegation_role_invalid": (422, "approval.delegation_role_invalid"),
    "approval.delegation_self": (422, "approval.delegation_self"),
    "approval.period_in_past": (422, "approval.period_in_past"),
    "approval.period_invalid": (422, "approval.period_invalid"),
    "approval.period_too_long": (422, "approval.period_too_long"),
    "approval.delegate_not_found": (422, "approval.delegate_not_found"),
    "approval.delegate_not_internal": (422, "approval.delegate_not_internal"),
    "approval.delegation_overlap": (409, "approval.delegation_overlap"),
    "material.certificate_required": (422, "material.certificate_required"),
    "material.unit_unknown": (422, "material.unit_unknown"),
    "stock.insufficient": (409, "stock.insufficient"),
    "stock.append_only": (409, "stock.append_only"),
    "stock.warehouse_required": (422, "stock.warehouse_required"),
    "stock.warehouse_project": (422, "stock.warehouse_project"),
    "stock.issue_incomplete": (422, "stock.issue_incomplete"),
    "stock.kind_invalid": (422, "stock.kind_invalid"),
    "stock.material_required": (422, "stock.material_required"),
    "stock.batch_required": (422, "stock.batch_required"),
    "stock.batch_not_applicable": (422, "stock.batch_not_applicable"),
    "stock.receipt_consumed": (409, "stock.receipt_consumed"),
    "batch.frozen": (409, "batch.frozen"),
    "document.file_required": (422, "document.file_required"),
    "stock.version_not_draft": (409, "stock.version_not_draft"),
    "stock.forbidden": (403, "forbidden"),
    "stock.not_found": (404, "not_found"),
    "stock.reversal_of_reversal": (409, "stock.reversal_of_reversal"),
    "stock.already_reversed": (409, "stock.already_reversed"),
    "approval.delegation_limit": (429, "approval.delegation_limit"),
    "user.notify_forbidden": (403, "forbidden"),
    "user.consent_version_required": (422, "consent.version_required"),
    "approval.delegation_not_found": (404, "not_found"),
    "document.parent_not_approved": (409, "document.parent_not_approved"),
    # Инвентаризация (ADR-016, S7c): снимок при отправке, корректировка при утверждении.
    "inventory.not_counted": (422, "inventory.not_counted"),
    "inventory.line_incomplete": (422, "inventory.line_incomplete"),
    "inventory.duplicate_line": (422, "inventory.duplicate_line"),
    "inventory.batch_required": (422, "inventory.batch_required"),
    "inventory.no_snapshot": (409, "inventory.no_snapshot"),
    "inventory.negative": (409, "inventory.negative"),
    "stock.adjustment_consumed": (409, "stock.adjustment_consumed"),
}

# Имя нарушенного ограничения или индекса -> (статус, код ответа).
CONSTRAINT_RULES: dict[str, tuple[int, str]] = {
    "uq_material_legal_entity_id_code": (409, "material.code_taken"),
    "uq_material_unit_material_id_unit": (409, "material.unit_taken"),
    "uq_material_batch_material_id_batch_no": (409, "material.batch_taken"),
    "uq_material_price_material_id": (409, "material.price_taken"),
    "uq_warehouse_project_id_name": (409, "warehouse.name_taken"),
    "uq_stock_movement_reversal_of_id": (409, "stock.already_reversed"),
    "uq_stock_movement_adjustment_source": (409, "stock.already_adjusted"),
    "ck_document_line_quantity_nonnegative": (422, "document.line_quantity_invalid"),
    "ck_stock_movement_qty_not_zero": (422, "stock.qty_invalid"),
    "ck_stock_movement_issue_node_and_responsible": (422, "stock.issue_incomplete"),
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
    "uq_equipment_rate_level_version": (409, "equipment.rate_version_exists"),
    "ck_equipment_rate_one_level": (422, "equipment.rate_target_invalid"),
    "ck_equipment_rate_valid_to_not_before_from": (422, "equipment.rate_period_invalid"),
    "ck_document_delegation_max_period": (422, "approval.period_too_long"),
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
    "ck_equipment_unit_year_range": (422, "equipment.year_invalid"),
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
    # Путевые листы, ГСМ, делегаты и журнал затрат (SE-2a).
    "uq_equipment_shift_key": (409, "equipment.shift_key_taken"),
    "uq_cost_entry_source_line": (409, "equipment.posting_conflict"),
    "uq_cost_entry_reversal_of_id": (409, "equipment.posting_conflict"),
    "ck_equipment_shift_hours_in_day": (422, "equipment.hours_invalid"),
    "ck_equipment_shift_line_hours_in_day": (422, "equipment.hours_invalid"),
    "ck_equipment_shift_reject_reason_required": (422, "equipment.reason_required"),
    "ck_equipment_shift_downtime_valid": (422, "equipment.downtime_invalid"),
    "ck_equipment_shift_downtime_reason_required": (422, "equipment.downtime_reason_required"),
    "ck_equipment_shift_odometer_valid": (422, "equipment.odometer_invalid"),
    "ck_equipment_shift_dates_in_range": (422, "equipment.dates_out_of_range"),
    "ck_equipment_shift_trips_not_negative": (422, "equipment.quantity_invalid"),
    "ck_equipment_shift_tonnes_not_negative": (422, "equipment.quantity_invalid"),
    "ck_equipment_shift_line_trips_not_negative": (422, "equipment.quantity_invalid"),
    "ck_equipment_shift_line_tonnes_not_negative": (422, "equipment.quantity_invalid"),
    "ck_equipment_shift_confirmed_fields": (409, "equipment.shift_transition_forbidden"),
    "ck_equipment_shift_confirmer_is_not_author": (409, "equipment.shift_transition_forbidden"),
    "ck_equipment_fuel_amount_not_negative": (422, "equipment.amount_invalid"),
    "ck_equipment_fuel_liters_not_negative": (422, "equipment.quantity_invalid"),
    "ck_equipment_fuel_dates_in_range": (422, "equipment.dates_out_of_range"),
    "ck_equipment_fuel_reversal_complete": (422, "equipment.fuel_invalid"),
    "ck_equipment_fuel_reverse_reason_not_blank": (422, "equipment.reason_required"),
    "ck_equipment_delegate_dates_ordered": (422, "equipment.dates_invalid"),
    "ck_equipment_delegate_dates_in_range": (422, "equipment.dates_out_of_range"),
    "ck_equipment_delegate_reason_not_blank": (422, "equipment.reason_required"),
    "ck_equipment_delegate_revocation_complete": (422, "equipment.delegate_invalid"),
    "fk_equipment_shift_project_id_project": (422, "equipment.project_invalid"),
    "fk_equipment_shift_equipment_unit_id_equipment_unit": (422, "equipment.unit_invalid"),
    "fk_equipment_shift_assignment_id_equipment_assignment": (422, "equipment.assignment_invalid"),
    "fk_equipment_shift_operator_id_equipment_operator": (422, "equipment.operator_invalid"),
    "fk_equipment_shift_line_project_id_equipment_shift": (422, "equipment.shift_invalid"),
    "fk_equipment_shift_line_project_id_project_node": (422, "equipment.node_invalid"),
    "fk_equipment_shift_line_project_id_schedule_item": (422, "equipment.schedule_item_invalid"),
    "fk_equipment_fuel_project_id_project": (422, "equipment.project_invalid"),
    "fk_equipment_fuel_equipment_unit_id_equipment_unit": (422, "equipment.unit_invalid"),
    "fk_equipment_delegate_project_id_project": (422, "equipment.project_invalid"),
    "fk_equipment_delegate_delegate_user_id_user": (422, "equipment.user_invalid"),
    # ГСМ в листе: чек (пара ключ и тип), сторно у заправки в листе, объект заправки и листа.
    "ck_equipment_fuel_receipt_pair": (422, "equipment.receipt_invalid"),
    "ck_equipment_fuel_receipt_key_not_blank": (422, "equipment.receipt_invalid"),
    "ck_equipment_fuel_shift_not_reversed": (409, "equipment.fuel_shift_bound"),
    "ck_equipment_fuel_shift_has_project": (422, "equipment.shift_invalid"),
    "fk_equipment_fuel_project_id_shift_id_equipment_shift": (422, "equipment.shift_invalid"),
    # Документы: уникальности, проверки полей и составные внешние ключи.
    "uq_document_legal_entity_id_registration_no": (409, "document.number_taken"),
    "uq_document_version_document_id_version_no": (409, "document.version_conflict"),
    "uq_document_version_s3_key": (409, "document.file_already_confirmed"),
    "uq_document_amount_document_id": (409, "document.amount_exists"),
    "uq_document_version_amount_version_id": (409, "document.amount_exists"),
    "ck_document_version_amount_amount_not_negative": (422, "document.amount_invalid"),
    "uq_legal_entity_code": (409, "legal_entity.code_taken"),
    "ck_document_kind_valid": (422, "document.kind_invalid"),
    "ck_document_scope_valid": (422, "document.scope_invalid"),
    "ck_document_counterparty_required": (422, "document.counterparty_required"),
    "ck_document_contract_required": (422, "document.contract_required"),
    "ck_document_title_not_blank": (422, "document.title_invalid"),
    "ck_document_version_content_type_valid": (422, "document.type_not_allowed"),
    "ck_document_version_size_valid": (422, "document.too_large"),
    "ck_document_version_key_in_document": (422, "document.key_invalid"),
    "ck_document_link_one_target": (422, "document.link_invalid"),
    "ck_document_amount_amount_not_negative": (422, "document.amount_invalid"),
    "fk_document_project_id_project": (422, "document.project_invalid"),
    "fk_document_counterparty_id_counterparty": (422, "document.counterparty_invalid"),
    "fk_document_contract_id_contract": (422, "document.contract_invalid"),
    "fk_document_link_work_actual_id_work_actual": (422, "document.link_invalid"),
    "fk_document_link_project_node_id_project_node": (422, "document.link_invalid"),
    "fk_document_link_equipment_unit_id_equipment_unit": (422, "document.link_invalid"),
    "ck_document_line_name_not_blank": (422, "document.line_invalid"),
    "ck_document_line_unit_not_blank": (422, "document.line_invalid"),
    "fk_document_line_work_id_work": (422, "document.line_invalid"),
    "fk_document_line_project_node_id_project_node": (422, "document.line_invalid"),
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
    if message.startswith("new row violates row-level security policy"):
        # Строка не проходит политику записи: отказ права (роль или чужой объект), не сбой сервера.
        return HTTPException(403, "forbidden")
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
