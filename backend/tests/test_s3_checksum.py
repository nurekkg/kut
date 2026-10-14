"""Контрольная сумма в подписанной загрузке (ADR-015 часть 1, раздел 3.6; вопрос PO 14).

Проверяет именно форк MinIO из compose: S3 сам сверяет SHA-256 тела с подписанным значением,
повтор в тот же ключ отклоняется (If-None-Match: *), а HEAD с ChecksumMode отдаёт сохранённый хэш.
Если форк не умеет checksum, эти тесты краснеют, и хэш считает воркер (вариант B ADR).
"""

import base64
import hashlib
import uuid

import httpx

from app.core.config import get_settings
from app.core.storage import WRITE_ONCE_HEADERS, get_s3_client


def _digest(body: bytes) -> str:
    return base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")


def _presign(key: str, body: bytes, digest: str) -> str:
    return get_s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": get_settings().s3_bucket,
            "Key": key,
            "ContentType": "application/pdf",
            "ContentLength": len(body),
            "ChecksumSHA256": digest,
            "IfNoneMatch": WRITE_ONCE_HEADERS["If-None-Match"],
        },
        ExpiresIn=120,
        HttpMethod="PUT",
    )


def _upload(url: str, body: bytes, digest: str) -> httpx.Response:
    return httpx.put(
        url,
        content=body,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(body)),
            "x-amz-checksum-sha256": digest,
            **WRITE_ONCE_HEADERS,
        },
        timeout=10,
    )


def test_s3_refuses_a_body_that_does_not_match_the_signed_checksum() -> None:
    key = f"document-probe/{uuid.uuid4()}.pdf"
    body = b"%PDF-1.4 probe"
    other = b"%PDF-1.4 XXXXX"  # той же длины: Content-Length входит в подпись
    response = _upload(_presign(key, body, _digest(body)), other, _digest(body))
    assert response.status_code == 400
    assert "BadDigest" in response.text or "Checksum" in response.text


def test_s3_stores_the_signed_checksum_and_returns_it_on_head() -> None:
    key = f"document-probe/{uuid.uuid4()}.pdf"
    body = b"%PDF-1.4 probe ok"
    digest = _digest(body)
    response = _upload(_presign(key, body, digest), body, digest)
    assert response.status_code == 200, response.text
    head = get_s3_client().head_object(
        Bucket=get_settings().s3_bucket, Key=key, ChecksumMode="ENABLED"
    )
    assert head["ChecksumSHA256"] == digest
    assert head["ContentLength"] == len(body)


def test_a_second_upload_to_the_same_key_is_refused() -> None:
    key = f"document-probe/{uuid.uuid4()}.pdf"
    body = b"%PDF-1.4 once"
    digest = _digest(body)
    first = _upload(_presign(key, body, digest), body, digest)
    assert first.status_code == 200, first.text
    second = _upload(_presign(key, body, digest), body, digest)
    assert second.status_code == 412
