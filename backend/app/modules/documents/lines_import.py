"""Позиции версии: чтение XLSX, проверка строк и шаблон (ADR-015 часть 4, раздел 5, S6a-1).

Файл читается как данные. Ячейка-формула - отказ с номерами строк, книга не пересчитывается.
Книги с макросами (xlsm, vbaProject) отклоняются по частям архива до чтения листа.
Всё или ничего: при любой ошибке строки не пишутся, ответ 422 несёт номера строк и коды.
"""

import importlib
import io
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status

# Стабов openpyxl в проекте нет: модуль берётся динамически, как в equipment/imports.py.
openpyxl = importlib.import_module("openpyxl")

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 2000
HEADER = ("№", "Наименование", "Ед.", "Количество", "Цена")
MACRO_PARTS = ("xl/vbaProject.bin",)
MACRO_TYPE = "macroEnabled"
NAME_MAX = 300
UNIT_MAX = 20
FORMULA_ROWS_SHOWN = 50


@dataclass(frozen=True, slots=True)
class ParsedLine:
    row: int
    position_no: int
    name: str
    unit: str
    quantity: Decimal
    unit_price: Decimal | None


def _rejected(code: str, rows: list[int] | None = None) -> HTTPException:
    detail: dict[str, Any] = {"code": code}
    if rows:
        detail["rows"] = rows
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def read_rows(data: bytes) -> list[tuple[int, list[Any]]]:
    """Строки первого листа с номерами строк файла (1 - заголовок). Формулы и макросы - отказ."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise _rejected("document.lines_format") from error
    names = set(archive.namelist())
    content_types = ""
    if "[Content_Types].xml" in names:
        content_types = archive.read("[Content_Types].xml").decode("utf-8", "ignore")
    if names & set(MACRO_PARTS) or MACRO_TYPE in content_types:
        raise _rejected("document.lines_macros")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as error:  # файл пользователя: любой сбой чтения - отказ формата, не 500
        raise _rejected("document.lines_format") from error
    try:
        sheet = workbook.worksheets[0]
        rows = [
            (number, list(row))
            for number, row in enumerate(sheet.iter_rows(values_only=True), start=1)
        ]
    finally:
        workbook.close()
    formula_rows = [
        number for number, row in rows if any(isinstance(v, str) and v.startswith("=") for v in row)
    ]
    if formula_rows:
        raise _rejected("document.lines_formula", formula_rows[:FORMULA_ROWS_SHOWN])
    return rows


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    elif isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _position(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number != number.to_integral_value() or number < 1 or number > 2000000:
        return None
    return int(number)


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and len(text) <= limit else None


def parse_lines(rows: list[tuple[int, list[Any]]]) -> list[ParsedLine]:
    if not rows:
        raise _rejected("document.lines_empty")
    header = tuple(
        "" if value is None else str(value).strip() for value in rows[0][1][: len(HEADER)]
    )
    if header != HEADER:
        raise _rejected("document.lines_header")
    data = [
        (number, (list(values) + [None] * len(HEADER))[: len(HEADER)])
        for number, values in rows[1:]
        if any(value not in (None, "") for value in values)
    ]
    if not data:
        raise _rejected("document.lines_empty")
    if len(data) > MAX_LINES:
        raise _rejected("document.lines_too_many")
    issues: list[dict[str, Any]] = []
    lines: list[ParsedLine] = []
    seen: set[int] = set()
    for number, (raw_no, raw_name, raw_unit, raw_quantity, raw_price) in data:
        position = _position(raw_no)
        name = _text(raw_name, NAME_MAX)
        unit = _text(raw_unit, UNIT_MAX)
        quantity = _decimal(raw_quantity)
        price = None if raw_price in (None, "") else _decimal(raw_price)
        checks = [
            (position is None or position in seen, "position", "№ - целое число от 1, без повтора"),
            (name is None, "name", f"Наименование - от 1 до {NAME_MAX} символов"),
            (unit is None, "unit", f"Ед. - от 1 до {UNIT_MAX} символов"),
            (
                quantity is None
                or quantity <= 0
                or abs(quantity) >= Decimal(10) ** 14
                or quantity != quantity.quantize(Decimal("0.0001")),
                "quantity",
                "Количество - число больше нуля, не больше четырёх знаков после запятой",
            ),
            (
                raw_price not in (None, "")
                and (
                    price is None
                    or price < 0
                    or abs(price) >= Decimal(10) ** 12
                    or price != price.quantize(Decimal("0.01"))
                ),
                "price",
                "Цена - число не меньше нуля, не больше двух знаков после запятой",
            ),
        ]
        row_issues = [
            {"row": number, "code": code, "message": message}
            for failed, code, message in checks
            if failed
        ]
        if row_issues or position is None or name is None or unit is None or quantity is None:
            issues.extend(row_issues)
            continue
        seen.add(position)
        lines.append(ParsedLine(number, position, name, unit, quantity, price))
    if issues:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "document.lines_invalid", "issues": issues},
        )
    return lines


def template_xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Позиции"
    sheet.append(list(HEADER))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
