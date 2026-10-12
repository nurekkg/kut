"""Импорт реестра техники из CSV и XLSX (ADR-014 часть 3, раздел 9, допущения rev2).

Всё или ничего: при любой ошибке строки файл не загружается, отчёт содержит номер строки файла
(строка 1 - заголовок), колонку, код и текст. Повтор того же файла - ноль создано, ноль обновлено.
Файл читается как данные: формулы не вычисляются (cached-значения), макросы отклоняются.
Запись пакетная: новые строки одним INSERT (колонки массивами, unnest), изменённые - UPDATE по
первичному ключу. Построчной записи нет.
"""

import csv
import importlib
import io
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Row, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.equipment.models import (
    Contract,
    EquipmentImportBatch,
    EquipmentType,
    EquipmentUnit,
)
from app.modules.equipment.schemas import ImportIssue, ImportReport
from app.modules.equipment.service import normalize_key
from app.modules.org.models import LegalEntity

MAX_ROWS = 5000
MAX_BYTES = 10 * 1024 * 1024
OWNERSHIPS = ("own", "rented")
COLUMNS = (
    ("legal_entity_tax_id", "ИНН юрлица"),
    ("equipment_type_code", "Код типа"),
    ("ownership", "Владение"),
    ("registry_number", "Номер"),
    ("contract_number", "Номер договора аренды"),
)
OPTIONAL_COLUMNS = (
    ("model", "Марка и модель"),
    ("year", "Год выпуска"),
)
MODEL_MAX = 200
MACRO_PARTS = ("xl/vbaProject.bin",)
MACRO_TYPE = "macroEnabled"
UNIT_COLUMNS = (
    "id",
    "legal_entity_id",
    "equipment_type_id",
    "ownership",
    "registry_key",
    "registry_number",
    "contract_id",
    "model",
    "year",
    "created_by",
    "updated_by",
)
INSERT_UNITS = text(
    """
    INSERT INTO equipment_unit (id, legal_entity_id, equipment_type_id, ownership, registry_key,
        registry_number, contract_id, model, year, created_by, updated_by)
    SELECT * FROM unnest(
        CAST(:id AS uuid[]), CAST(:legal_entity_id AS uuid[]), CAST(:equipment_type_id AS uuid[]),
        CAST(:ownership AS text[]), CAST(:registry_key AS text[]), CAST(:registry_number AS text[]),
        CAST(:contract_id AS uuid[]), CAST(:model AS text[]), CAST(:year AS integer[]),
        CAST(:created_by AS uuid[]), CAST(:updated_by AS uuid[])
    )
    """
)
UPDATE_COLUMNS = (
    "id",
    "registry_number",
    "equipment_type_id",
    "ownership",
    "contract_id",
    "model",
    "year",
    "updated_by",
)
UPDATE_UNITS = text(
    """
    UPDATE equipment_unit AS u
    SET registry_number = v.registry_number, equipment_type_id = v.equipment_type_id,
        ownership = v.ownership, contract_id = v.contract_id, model = v.model, year = v.year,
        updated_by = v.updated_by, updated_at = now()
    FROM unnest(
        CAST(:id AS uuid[]), CAST(:registry_number AS text[]), CAST(:equipment_type_id AS uuid[]),
        CAST(:ownership AS text[]), CAST(:contract_id AS uuid[]), CAST(:model AS text[]),
        CAST(:year AS integer[]), CAST(:updated_by AS uuid[])
    ) AS v (id, registry_number, equipment_type_id, ownership, contract_id, model, year, updated_by)
    WHERE u.id = v.id
    """
)


@dataclass(frozen=True, slots=True)
class Record:
    row: int
    legal_entity_id: uuid.UUID
    equipment_type_id: uuid.UUID
    ownership: str
    registry_number: str
    registry_key: str
    contract_id: uuid.UUID | None
    model: str | None
    year: int | None


@dataclass(slots=True)
class Checked:
    records: list[Record]
    issues: list[ImportIssue]
    rows_total: int


def _openpyxl() -> ModuleType:
    """openpyxl нужен только для XLSX: без пакета импорт отвечает 501, остальное работает."""
    try:
        return importlib.import_module("openpyxl")
    except ImportError as error:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "equipment.xlsx_unavailable"
        ) from error


def _rejected(code: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, code)


def decode_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _rejected("equipment.import_encoding") from error
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def decode_xlsx(data: bytes) -> list[list[str]]:
    """Первый лист, значения без пересчёта формул; книга с макросами не принимается."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise _rejected("equipment.import_format") from error
    names = set(archive.namelist())
    content_types = ""
    if "[Content_Types].xml" in names:
        content_types = archive.read("[Content_Types].xml").decode("utf-8", "ignore")
    if names & set(MACRO_PARTS) or MACRO_TYPE in content_types:
        raise _rejected("equipment.import_macros_forbidden")
    openpyxl = _openpyxl()
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        return [
            ["" if value is None else str(value) for value in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()


def parse(filename: str, data: bytes) -> list[list[str]]:
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "equipment.import_too_large")
    lowered = filename.lower()
    if lowered.endswith(".xlsx"):
        return decode_xlsx(data)
    if lowered.endswith(".csv"):
        return decode_csv(data)
    raise _rejected("equipment.import_format")


def _header_map(header: Sequence[str]) -> tuple[dict[str, int], list[ImportIssue]]:
    names = [cell.strip().casefold() for cell in header]
    found: dict[str, int] = {}
    issues: list[ImportIssue] = []
    for key, title in COLUMNS:
        if title.casefold() in names:
            found[key] = names.index(title.casefold())
        else:
            issues.append(
                ImportIssue(
                    row=1, column=title, code="header_missing", message=f"Нет колонки «{title}»"
                )
            )
    for key, title in OPTIONAL_COLUMNS:
        if title.casefold() in names:
            found[key] = names.index(title.casefold())
    return found, issues


def _issue(row: int, column: str, code: str, message: str) -> ImportIssue:
    return ImportIssue(row=row, column=column, code=code, message=message)


def _cell(cells: Sequence[str], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index].strip()


async def _lookups(
    session: AsyncSession,
) -> tuple[
    dict[str, uuid.UUID],
    dict[str, uuid.UUID],
    dict[tuple[uuid.UUID, str], tuple[uuid.UUID, str]],
]:
    legal = {
        tax_id: legal_id
        for legal_id, tax_id in (
            await session.execute(select(LegalEntity.id, LegalEntity.tax_id))
        ).all()
        if tax_id
    }
    types = {
        code: type_id
        for type_id, code in (
            await session.execute(
                select(EquipmentType.id, EquipmentType.code).where(
                    EquipmentType.deleted_at.is_(None)
                )
            )
        ).all()
    }
    contracts = {
        (legal_id, number): (contract_id, status_value)
        for contract_id, legal_id, number, status_value in (
            await session.execute(
                select(
                    Contract.id, Contract.legal_entity_id, Contract.number, Contract.status
                ).where(Contract.kind == "rental_equipment", Contract.deleted_at.is_(None))
            )
        ).all()
    }
    return legal, types, contracts


def _optional(cells: Sequence[str], columns: dict[str, int], key: str) -> str:
    return _cell(cells, columns.get(key))


async def check(session: AsyncSession, rows: list[list[str]]) -> Checked:
    """Проверка без записи: номера строк файла, колонки, коды. Ошибки - по порядку строк."""
    if not rows:
        return Checked(
            [], [ImportIssue(row=1, column="", code="empty_file", message="Файл пуст")], 0
        )
    columns, issues = _header_map(rows[0])
    body = [
        (number, cells)
        for number, cells in enumerate(rows[1:], start=2)
        if any(c.strip() for c in cells)
    ]
    if len(body) > MAX_ROWS:
        issues.append(
            ImportIssue(
                row=1, column="", code="too_many_rows", message=f"Не больше {MAX_ROWS} строк"
            )
        )
    if issues or len(body) > MAX_ROWS:
        return Checked([], issues, len(body))
    legal, types, contracts = await _lookups(session)
    records: list[Record] = []
    seen: dict[tuple[uuid.UUID, str], int] = {}
    for number, cells in body:
        row_issues: list[ImportIssue] = []

        tax_id = _cell(cells, columns["legal_entity_tax_id"])
        legal_id = legal.get(tax_id)
        if legal_id is None:
            row_issues.append(
                _issue(
                    number,
                    "ИНН юрлица",
                    "legal_entity_unknown",
                    f"Юрлицо с ИНН «{tax_id}» не найдено",
                )
            )
        type_code = _cell(cells, columns["equipment_type_code"])
        type_id = types.get(type_code)
        if type_id is None:
            row_issues.append(
                _issue(number, "Код типа", "type_unknown", f"Нет вида техники «{type_code}»")
            )
        ownership = _cell(cells, columns["ownership"]).lower()
        if ownership not in OWNERSHIPS:
            row_issues.append(
                _issue(
                    number,
                    "Владение",
                    "ownership_invalid",
                    "Укажите own (своя) или rented (арендованная)",
                )
            )
        registry_number = _cell(cells, columns["registry_number"])
        if not registry_number:
            row_issues.append(
                _issue(
                    number,
                    "Номер",
                    "registry_number_required",
                    "Не указан гос. или заводской номер",
                )
            )
        model = _optional(cells, columns, "model") or None
        if model is not None and len(model) > MODEL_MAX:
            row_issues.append(
                _issue(
                    number,
                    "Марка и модель",
                    "model_too_long",
                    f"Не длиннее {MODEL_MAX} знаков",
                )
            )
        year: int | None = None
        year_text = _optional(cells, columns, "year")
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                year = None
            if year is None or not 1950 <= year <= 2100:
                row_issues.append(
                    _issue(
                        number,
                        "Год выпуска",
                        "year_invalid",
                        "Год выпуска - целое число от 1950 до 2100",
                    )
                )
        contract_number = _cell(cells, columns["contract_number"])
        contract_id = None
        if ownership == "rented" and not contract_number:
            row_issues.append(
                _issue(
                    number,
                    "Номер договора аренды",
                    "contract_required",
                    "Арендованной технике нужен договор",
                )
            )
        if ownership == "own" and contract_number:
            row_issues.append(
                _issue(
                    number,
                    "Номер договора аренды",
                    "contract_forbidden_for_own",
                    "У своей техники договора нет",
                )
            )
        if ownership == "rented" and contract_number and legal_id is not None:
            found = contracts.get((legal_id, contract_number))
            if found is None:
                row_issues.append(
                    _issue(
                        number,
                        "Номер договора аренды",
                        "contract_not_found",
                        f"Нет договора аренды «{contract_number}»",
                    )
                )
            elif found[1] != "active":
                row_issues.append(
                    _issue(
                        number,
                        "Номер договора аренды",
                        "contract_not_active",
                        f"Договор «{contract_number}» не активен",
                    )
                )
            else:
                contract_id = found[0]
        if row_issues or legal_id is None or type_id is None:
            issues.extend(row_issues)
            continue
        key = normalize_key(registry_number)
        first = seen.get((legal_id, key))
        if first is not None:
            issues.append(
                ImportIssue(
                    row=number,
                    column="Номер",
                    code="duplicate_key",
                    message=f"Тот же номер уже в строке {first}",
                )
            )
            continue
        seen[(legal_id, key)] = number
        records.append(
            Record(
                row=number,
                legal_entity_id=legal_id,
                equipment_type_id=type_id,
                ownership=ownership,
                registry_number=registry_number,
                registry_key=key,
                contract_id=contract_id,
                model=model,
                year=year,
            )
        )
    return Checked(records, issues, len(body))


async def _existing(session: AsyncSession) -> dict[tuple[uuid.UUID, str], Row[Any]]:
    """Живые единицы реестра одним запросом: только нужные поля, без ORM-объектов."""
    rows = await session.execute(
        select(
            EquipmentUnit.id,
            EquipmentUnit.legal_entity_id,
            EquipmentUnit.registry_key,
            EquipmentUnit.registry_number,
            EquipmentUnit.equipment_type_id,
            EquipmentUnit.ownership,
            EquipmentUnit.contract_id,
            EquipmentUnit.model,
            EquipmentUnit.year,
        ).where(EquipmentUnit.deleted_at.is_(None))
    )
    return {(row.legal_entity_id, row.registry_key): row for row in rows}


def _unchanged(unit: Row[Any], record: Record) -> bool:
    return bool(
        unit.registry_number == record.registry_number
        and unit.equipment_type_id == record.equipment_type_id
        and unit.ownership == record.ownership
        and unit.contract_id == record.contract_id
        and unit.model == record.model
        and unit.year == record.year
    )


def _counts(
    records: Sequence[Record], existing: dict[tuple[uuid.UUID, str], Row[Any]]
) -> tuple[int, int, int]:
    """Создать, обновить, оставить - по каждой записи файла (ключ: юрлицо и номер)."""
    created = updated = unchanged = 0
    for record in records:
        unit = existing.get((record.legal_entity_id, record.registry_key))
        if unit is None:
            created += 1
        elif _unchanged(unit, record):
            unchanged += 1
        else:
            updated += 1
    return created, updated, unchanged


async def diff_counts(session: AsyncSession, records: Sequence[Record]) -> tuple[int, int, int]:
    """Счётчики без записи (для проверки)."""
    return _counts(records, await _existing(session))


def _arrays(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> dict[str, list[Any]]:
    """Колонки строк массивами для unnest; UUID передаются текстом и приводятся в SQL."""
    arrays: dict[str, list[Any]] = {name: [] for name in columns}
    for row in rows:
        for name in columns:
            value = row[name]
            arrays[name].append(str(value) if isinstance(value, uuid.UUID) else value)
    return arrays


async def apply(
    session: AsyncSession,
    records: Sequence[Record],
    *,
    file_sha256: str,
    rows_total: int,
    actor: uuid.UUID,
) -> ImportReport:
    """Запись реестра в текущей транзакции (вызывает идемпотентная обёртка).

    Новые строки - одним INSERT (колонки массивами, unnest), изменённые - пакетным UPDATE по
    первичному ключу; аудит срабатывает по оператору, а не по строке.
    """
    existing = await _existing(session)
    created, updated, unchanged = _counts(records, existing)
    new_rows: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []
    for record in records:
        unit = existing.get((record.legal_entity_id, record.registry_key))
        values = {
            "legal_entity_id": record.legal_entity_id,
            "equipment_type_id": record.equipment_type_id,
            "ownership": record.ownership,
            "registry_number": record.registry_number,
            "contract_id": record.contract_id,
            "model": record.model,
            "year": record.year,
        }
        if unit is None:
            new_rows.append(
                {
                    **values,
                    "id": uuid.uuid4(),
                    "registry_key": record.registry_key,
                    "created_by": actor,
                    "updated_by": actor,
                }
            )
        elif not _unchanged(unit, record):
            changed_rows.append({**values, "id": unit.id, "updated_by": actor})
    if new_rows:
        # Один оператор на все новые строки: executemany выполнил бы аудит и проверки политик
        # по разу на каждую строку.
        await session.execute(INSERT_UNITS, _arrays(new_rows, UNIT_COLUMNS))
    if changed_rows:
        # Тот же приём для изменённых строк: один UPDATE вместо оператора на каждую строку.
        await session.execute(UPDATE_UNITS, _arrays(changed_rows, UPDATE_COLUMNS))
    batch = EquipmentImportBatch(
        kind="units",
        file_sha256=file_sha256,
        status="committed",
        created_count=created,
        updated_count=updated,
        unchanged_count=unchanged,
        error_count=0,
        errors=[],
    )
    session.add(batch)
    await session.flush()
    return ImportReport(
        batch_id=batch.id,
        status="committed",
        rows_total=rows_total,
        created=created,
        updated=updated,
        unchanged=unchanged,
        errors=[],
    )


async def journal(
    session: AsyncSession,
    *,
    file_sha256: str,
    status_value: str,
    issues: Sequence[ImportIssue],
    counts: tuple[int, int, int] = (0, 0, 0),
) -> uuid.UUID:
    """Строка журнала: проверка и отказ тоже записываются (только вставка)."""
    batch = EquipmentImportBatch(
        kind="units",
        file_sha256=file_sha256,
        status=status_value,
        created_count=counts[0],
        updated_count=counts[1],
        unchanged_count=counts[2],
        error_count=len(issues),
        errors=[issue.model_dump() for issue in issues],
    )
    session.add(batch)
    await session.flush()
    return batch.id


def issues_csv(issues: Sequence[ImportIssue]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["строка", "колонка", "код", "текст"])
    for issue in issues:
        writer.writerow([issue.row, issue.column, issue.code, issue.message])
    return buffer.getvalue()


def template_rows() -> list[list[str]]:
    return [[title for _key, title in (*COLUMNS, *OPTIONAL_COLUMNS)]]


def template_xlsx() -> bytes:
    workbook = _openpyxl().Workbook(write_only=True)
    sheet = workbook.create_sheet("Реестр")
    sheet.append(template_rows()[0])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def template_csv() -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(template_rows())
    return buffer.getvalue()
