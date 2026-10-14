"""Проверка содержимого загруженного файла при подтверждении (ADR-015 часть 2 раздел 8.4).

Тип из HEAD сверяется с сигнатурой первых байтов; у OOXML проверяются состав архива и макросы;
у PDF ищутся активные элементы (JavaScript, Launch, EmbeddedFile). Файл читается из S3 диапазонами
или потоком: целиком в память не грузится.
"""

import asyncio
import re
import zipfile

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core import storage

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Токены, которые PDF не должен содержать: скрипт, запуск внешней программы, вложенный файл.
_ACTIVE_PDF = re.compile(rb"/(JavaScript|JS(?![A-Za-z])|Launch|EmbeddedFile)")
_OVERLAP = 64  # длиннее любого токена: токен, разрезанный границей кусков, всё равно найдётся
_MAX_TABLE_BYTES = 1024 * 1024  # [Content_Types].xml не бывает большим; больше - подозрительно


def _refuse(code: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, code)


def _signature_matches(head: bytes, content_type: str) -> bool:
    if content_type == PDF:
        return head.startswith(b"%PDF-")
    if content_type in (XLSX, DOCX):
        return head.startswith(b"PK\x03\x04")
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return head[0:4] == b"RIFF" and head[8:12] == b"WEBP"
    if content_type == "image/heic":
        return head[4:8] == b"ftyp"
    return False


def _ooxml_check(key: str, size: int, content_type: str) -> None:
    """Состав OOXML: нужная часть есть, макросов нет (ни в архиве, ни в объявленных типах)."""
    try:
        archive = zipfile.ZipFile(storage.S3RangeFile(key, size))
    except zipfile.BadZipFile as error:
        raise _refuse("document.type_mismatch") from error
    names = set(archive.namelist())
    if any(name.endswith("vbaProject.bin") for name in names):
        raise _refuse("document.macros_forbidden")
    marker = "xl/workbook.xml" if content_type == XLSX else "word/document.xml"
    if marker not in names or "[Content_Types].xml" not in names:
        raise _refuse("document.type_mismatch")
    table = archive.getinfo("[Content_Types].xml")
    if table.file_size > _MAX_TABLE_BYTES:
        raise _refuse("document.type_mismatch")
    if b"macroEnabled" in archive.read("[Content_Types].xml"):
        raise _refuse("document.macros_forbidden")


def _pdf_has_active_content(key: str) -> bool:
    tail = b""
    for chunk in storage.iter_object(key):
        window = tail + chunk
        if _ACTIVE_PDF.search(window):
            return True
        tail = window[-_OVERLAP:]
    return False


async def inspect(key: str, size: int, content_type: str) -> None:
    """Бросает 422 с кодом причины; хранилище недоступно - 503 (как у фото)."""
    try:
        head = await asyncio.to_thread(storage.read_range, key, 0, min(16, size))
        if not _signature_matches(head, content_type):
            raise _refuse("document.type_mismatch")
        if content_type in (XLSX, DOCX):
            await asyncio.to_thread(_ooxml_check, key, size, content_type)
        if content_type == PDF and await asyncio.to_thread(_pdf_has_active_content, key):
            raise _refuse("document.active_content")
    except (ClientError, BotoCoreError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable") from error
