import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

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


def presign_put(key: str, content_type: str, size_bytes: int, ttl_seconds: int) -> str:
    """Ссылка на загрузку одного файла: тип и длина входят в подпись, другой файл S3 не примет."""
    return get_s3_signer().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": get_settings().s3_bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        },
        ExpiresIn=ttl_seconds,
        HttpMethod="PUT",
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


@dataclass(frozen=True, slots=True)
class StoredObject:
    size: int
    content_type: str


async def head_object(key: str) -> StoredObject | None:
    """Метаданные объекта из S3 или None, если его там нет; прочие ошибки S3 пробрасываются."""
    try:
        response = await asyncio.to_thread(
            get_s3_client().head_object, Bucket=get_settings().s3_bucket, Key=key
        )
    except ClientError as error:
        if str(error.response.get("Error", {}).get("Code", "")) in NOT_FOUND_CODES:
            return None
        raise
    return StoredObject(
        size=int(response["ContentLength"]), content_type=str(response.get("ContentType", ""))
    )
