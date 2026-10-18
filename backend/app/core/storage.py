import asyncio
import io
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def _client(endpoint_url: str) -> "S3Client":
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 1},
        ),
    )


@lru_cache
def get_s3_client() -> "S3Client":
    return _client(get_settings().s3_endpoint_url)


@lru_cache
def get_s3_signer() -> "S3Client":
    """Клиент для подписи ссылок: адрес в подписи должен быть тем, по которому ходит браузер
    (в docker-сети это не minio:9000). Подпись считается локально, в сеть клиент не ходит."""
    settings = get_settings()
    return _client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


async def check_s3() -> None:
    await asyncio.to_thread(get_s3_client().head_bucket, Bucket=get_settings().s3_bucket)


WRITE_ONCE_HEADERS = {"If-None-Match": "*"}


def presign_put(
    key: str,
    content_type: str,
    size_bytes: int,
    ttl_seconds: int,
    checksum_sha256: str | None = None,
) -> str:
    """Ссылка на загрузку одного файла: тип и длина входят в подпись, другой файл S3 не примет.

    If-None-Match: * тоже в подписи: ключ записывается один раз. Без него автор мог бы до конца
    срока ссылки подменить файл, уже подтверждённый и показанный как доказательство факта.
    checksum_sha256 (base64) - контрольная сумма тела: S3 отклоняет файл, чей SHA-256 другой
    (ADR-015 часть 1 раздел 3.6, проверено на форке MinIO).
    """
    params: dict[str, object] = {
        "Bucket": get_settings().s3_bucket,
        "Key": key,
        "ContentType": content_type,
        "ContentLength": size_bytes,
        "IfNoneMatch": WRITE_ONCE_HEADERS["If-None-Match"],
    }
    if checksum_sha256 is not None:
        params["ChecksumSHA256"] = checksum_sha256
    return get_s3_signer().generate_presigned_url(
        "put_object", Params=params, ExpiresIn=ttl_seconds, HttpMethod="PUT"
    )


def presign_get(key: str, content_type: str, ttl_seconds: int) -> str:
    return get_s3_signer().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": get_settings().s3_bucket,
            "Key": key,
            "ResponseContentType": content_type,
            "ResponseContentDisposition": "inline",
        },
        ExpiresIn=ttl_seconds,
        HttpMethod="GET",
    )


def presign_download(key: str, content_type: str, filename: str, ttl_seconds: int) -> str:
    """Скачивание документа: attachment, чтобы офисный файл или PDF не открывались в браузере."""
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return get_s3_signer().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": get_settings().s3_bucket,
            "Key": key,
            "ResponseContentType": content_type,
            "ResponseContentDisposition": disposition,
        },
        ExpiresIn=ttl_seconds,
        HttpMethod="GET",
    )


@dataclass(frozen=True, slots=True)
class StoredObject:
    size: int
    content_type: str
    checksum_sha256: str | None = None


async def head_object(key: str) -> StoredObject | None:
    """Метаданные объекта из S3 или None, если его там нет; прочие ошибки S3 пробрасываются.

    checksum_sha256 - значение, которое S3 сверил при загрузке (base64), если оно было подписано.
    """
    try:
        response = await asyncio.to_thread(
            get_s3_client().head_object,
            Bucket=get_settings().s3_bucket,
            Key=key,
            ChecksumMode="ENABLED",
        )
    except ClientError as error:
        if str(error.response.get("Error", {}).get("Code", "")) in NOT_FOUND_CODES:
            return None
        raise
    return StoredObject(
        size=int(response["ContentLength"]),
        content_type=str(response.get("ContentType", "")),
        checksum_sha256=response.get("ChecksumSHA256"),
    )


def read_range(key: str, start: int, length: int) -> bytes:
    """Кусок объекта (синхронно, вызывать из потока). Конец файла не превышает длину."""
    end = start + length - 1
    response = get_s3_client().get_object(
        Bucket=get_settings().s3_bucket, Key=key, Range=f"bytes={start}-{end}"
    )
    return response["Body"].read()


def iter_object(key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Тело объекта кусками (синхронно, вызывать из потока): для проверки PDF без загрузки целиком
    в память."""
    body = get_s3_client().get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"]
    yield from body.iter_chunks(chunk_size)


class S3RangeFile(io.RawIOBase):
    """Только чтение с произвольным доступом поверх диапазонов S3: zipfile читает оглавление ZIP
    без загрузки всего файла (синхронно, вызывать из потока)."""

    def __init__(self, key: str, size: int) -> None:
        self._key = key
        self._size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._size - self._pos
        length = min(size, self._size - self._pos)
        if length <= 0:
            return b""
        data = read_range(self._key, self._pos, length)
        self._pos += len(data)
        return data
