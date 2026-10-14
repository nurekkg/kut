"""Проверка содержимого загруженного файла при подтверждении (ADR-015 часть 2 раздел 8.4).

Тип из HEAD сверяется с сигнатурой первых байтов; у OOXML проверяются состав архива и макросы
(регистр имён не важен); у PDF ищутся активные элементы (JavaScript, Launch, EmbeddedFile) в
исходных байтах и в распакованных FlateDecode-потоках (объектные потоки сжаты). Это эвристика,
не разбор PDF: потоки с другими фильтрами (LZW, ASCIIHex, ASCII85) и шифрованные PDF она не
раскрывает; полный разбор или антивирус - до пилота (вопрос PO 6).
"""

import asyncio
import re
import zipfile
import zlib
from collections.abc import Iterator

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core import storage

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Токены, которых в PDF быть не должно: скрипт, запуск внешней программы, вложенный файл.
_ACTIVE_PDF = re.compile(rb"/(JavaScript|JS(?![A-Za-z])|Launch|EmbeddedFile)", re.IGNORECASE)
# PDF записывает имена с #xx (/J#61vaScript): раскодируем перед поиском.
_HEX_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_STREAM = re.compile(rb"stream\r?\n")
# Распакованные потоки одного файла: сверх бюджета файл не проверить - значит, отказ.
_INFLATE_BUDGET = 256 * 1024 * 1024
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
    """Состав OOXML: нужная часть есть, макросов нет. Имена частей OPC регистронезависимы."""
    try:
        archive = zipfile.ZipFile(storage.S3RangeFile(key, size))
    except zipfile.BadZipFile as error:
        raise _refuse("document.type_mismatch") from error
    original = {name.lower(): name for name in archive.namelist()}
    if any(name.endswith("vbaproject.bin") for name in original):
        raise _refuse("document.macros_forbidden")
    marker = "xl/workbook.xml" if content_type == XLSX else "word/document.xml"
    if marker not in original or "[content_types].xml" not in original:
        raise _refuse("document.type_mismatch")
    table = archive.getinfo(original["[content_types].xml"])
    if table.file_size > _MAX_TABLE_BYTES:
        raise _refuse("document.type_mismatch")
    if b"macroenabled" in archive.read(original["[content_types].xml"]).lower():
        raise _refuse("document.macros_forbidden")


def _names_unescaped(data: bytes) -> bytes:
    return _HEX_ESCAPE.sub(lambda match: bytes([int(match.group(1), 16)]), data)


def _inflated_streams(data: bytes) -> Iterator[bytes]:
    """Распакованные FlateDecode-потоки. Не распаковывается - поток не FlateDecode, его пропускаем.
    Если распакованное не помещается в бюджет файла, проверить его нельзя: отказ."""
    view = memoryview(data)
    budget = _INFLATE_BUDGET
    for match in _STREAM.finditer(data):
        decompressor = zlib.decompressobj()
        try:
            chunk = decompressor.decompress(view[match.end() :], budget + 1)
        except zlib.error:
            continue
        if len(chunk) > budget:
            raise _refuse("document.pdf_unreadable")
        budget -= len(chunk)
        yield chunk


def _pdf_has_active_content(key: str) -> bool:
    data = b"".join(storage.iter_object(key))
    if _ACTIVE_PDF.search(_names_unescaped(data)):
        return True
    return any(_ACTIVE_PDF.search(_names_unescaped(chunk)) for chunk in _inflated_streams(data))


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
