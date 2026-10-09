"""Демо-фото факта для разработки: несколько маленьких PNG в S3 (MinIO) и записи о них.

Вызывается из main() app.modules.auth.devseed после основного сида: файлы кладутся в хранилище,
поэтому в тестовом сиде (там S3 не нужен) эта часть не участвует. Повторный запуск ничего не
меняет: пока у объекта есть живые фото, сид пропускается.
"""

import struct
import uuid
import zlib
from typing import Any

from sqlalchemy import Connection, text

from app.core import storage
from app.core.config import get_settings

CONTENT_TYPE = "image/png"
SIZE = (160, 120)
COLORS = ((70, 110, 160), (160, 110, 70), (90, 150, 100), (150, 90, 130))
LOCATION = ("42.874600", "74.569800")


def make_png(width: int, height: int, base: tuple[int, int, int]) -> bytes:
    """Маленький PNG с градиентом: без внешних библиотек, чтобы сид не тянул Pillow."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # фильтр строки: без фильтра
        for x in range(width):
            raw += bytes(
                (
                    (base[0] + y * 60 // height) % 256,
                    (base[1] + x * 60 // width) % 256,
                    base[2] % 256,
                )
            )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        bytes((137, 80, 78, 71, 13, 10, 26, 10))
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def ensure_photos(connection: Connection, project_id: Any, legal_entity_id: Any) -> int:
    """4 фото у подтверждённых фактов первых этажей: файлы в S3 и записи work_photo."""
    if connection.execute(
        text("SELECT id FROM work_photo WHERE project_id = :p AND deleted_at IS NULL LIMIT 1"),
        {"p": project_id},
    ).first():
        return 0
    facts = list(
        connection.execute(
            text(
                "SELECT id, author_id FROM work_actual WHERE project_id = :p"
                " AND status = 'approved' AND deleted_at IS NULL AND schedule_item_id IS NOT NULL"
                " ORDER BY work_date, created_at, id LIMIT :n"
            ),
            {"p": project_id, "n": len(COLORS)},
        )
    )
    client = storage.get_s3_client()
    bucket = get_settings().s3_bucket
    created = 0
    for fact, color in zip(facts, COLORS, strict=False):
        key = f"project/{project_id}/fact/{fact.id}/{uuid.uuid4()}.png"
        body = make_png(*SIZE, color)
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=CONTENT_TYPE)
        connection.execute(
            text(
                "INSERT INTO work_photo (project_id, legal_entity_id, work_actual_id, s3_key,"
                " content_type, size_bytes, latitude, longitude, taken_at, uploaded_by)"
                " VALUES (:project, :entity, :fact, :key, :type, :size, CAST(:lat AS numeric),"
                " CAST(:lon AS numeric), now(), :uploader)"
            ),
            {
                "project": project_id,
                "entity": legal_entity_id,
                "fact": fact.id,
                "key": key,
                "type": CONTENT_TYPE,
                "size": len(body),
                "lat": LOCATION[0],
                "lon": LOCATION[1],
                "uploader": fact.author_id,
            },
        )
        created += 1
    return created
