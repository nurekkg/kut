import asyncio
import re
import uuid
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import get_settings
from app.core.rules import db_rules
from app.modules.actuals.models import WorkActual
from app.modules.identity.models import User
from app.modules.objects.models import Project, ProjectNode
from app.modules.photos.models import EXTENSIONS, MAX_PHOTO_BYTES, WorkPhoto
from app.modules.photos.schemas import (
    DownloadUrl,
    PhotoConfirm,
    PhotoOut,
    PresignedUpload,
    PresignRequest,
    PresignResponse,
)
from app.modules.works.models import Work

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(rf"project/{_UUID}/fact/{_UUID}/{_UUID}\.(?:jpg|png|heic|webp)")

_COLUMNS = (
    WorkPhoto.id,
    WorkPhoto.work_actual_id,
    WorkPhoto.content_type,
    WorkPhoto.size_bytes,
    WorkPhoto.latitude,
    WorkPhoto.longitude,
    WorkPhoto.taken_at,
    WorkPhoto.uploaded_by,
    WorkPhoto.created_at,
)


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def storage_unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")


def make_key(project_id: uuid.UUID, actual_id: uuid.UUID, content_type: str) -> str:
    """Ключ задаёт сервер: клиент не выбирает, куда в хранилище попадёт файл."""
    return f"project/{project_id}/fact/{actual_id}/{uuid.uuid4()}.{EXTENSIONS[content_type]}"


async def _require_may_add(
    session: AsyncSession, project_id: uuid.UUID, actual_id: uuid.UUID
) -> None:
    """Факт виден пользователю (RLS и объект из пути) и ему можно добавлять фото (правило БД)."""
    found = await session.scalar(
        select(WorkActual.id).where(WorkActual.id == actual_id, WorkActual.project_id == project_id)
    )
    if found is None:
        raise not_found()
    allowed = await session.scalar(
        text("SELECT app.work_photo_rights(:fact, 'add')"), {"fact": actual_id}
    )
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


async def presign(
    session: AsyncSession, project_id: uuid.UUID, data: PresignRequest
) -> PresignResponse:
    await _require_may_add(session, project_id, data.work_actual_id)
    ttl = get_settings().photo_upload_ttl_seconds
    items = []
    for file in data.files:
        key = make_key(project_id, data.work_actual_id, file.content_type)
        items.append(
            PresignedUpload(
                key=key,
                upload_url=storage.presign_put(key, file.content_type, file.size_bytes, ttl),
                headers={"Content-Type": file.content_type, **storage.WRITE_ONCE_HEADERS},
                expires_in=ttl,
            )
        )
    return PresignResponse(items=items)


def _out(row: Any, *, url: str | None = None, ttl: int | None = None) -> PhotoOut:
    return PhotoOut.model_validate(
        {**row._asdict(), "url": url, "url_expires_in": ttl if url else None}
    )


async def confirm(
    session: AsyncSession, project_id: uuid.UUID, data: PhotoConfirm, uploader_id: uuid.UUID
) -> list[PhotoOut]:
    """Без COMMIT: вызывается внутри идемпотентной транзакции.

    Запись создаётся только после того, как сервер увидел файл в S3 (HEAD): тип и размер берутся
    из хранилища, а не из слов клиента. Ключ должен быть выданным этому факту (project/<объект>/
    fact/<факт>/<uuid>.<расширение>).
    """
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(Project.id == project_id)
    )
    if legal_entity_id is None:
        raise not_found()
    await _require_may_add(session, project_id, data.work_actual_id)
    prefix = f"project/{project_id}/fact/{data.work_actual_id}/"
    for item in data.items:
        if not (item.key.startswith(prefix) and _KEY.fullmatch(item.key)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "photo_key_invalid")
    # Проверки файлов идут одновременно: последовательные HEAD с таймаутом держали бы соединение
    # пула и ключ идемпотентности до 10 x 4 с при деградации хранилища.
    try:
        stored_objects = await asyncio.gather(*(storage.head_object(i.key) for i in data.items))
    except (ClientError, BotoCoreError) as error:
        raise storage_unavailable() from error
    photos = []
    for item, stored in zip(data.items, stored_objects, strict=True):
        if stored is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "photo_not_uploaded")
        if stored.content_type not in EXTENSIONS or not 0 < stored.size <= MAX_PHOTO_BYTES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "photo_invalid")
        photo = WorkPhoto(
            project_id=project_id,
            legal_entity_id=legal_entity_id,
            work_actual_id=data.work_actual_id,
            s3_key=item.key,
            content_type=stored.content_type,
            size_bytes=stored.size,
            latitude=item.latitude,
            longitude=item.longitude,
            taken_at=item.taken_at,
            uploaded_by=uploader_id,
        )
        session.add(photo)
        photos.append(photo)
    async with db_rules(session):
        await session.flush()
    rows = await session.execute(
        select(*_COLUMNS)
        .where(WorkPhoto.id.in_([photo.id for photo in photos]))
        .order_by(WorkPhoto.created_at, WorkPhoto.id)
    )
    return [_out(row) for row in rows]


async def list_photos(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    actual_ids: list[uuid.UUID] | None,
    node_id: uuid.UUID | None = None,
    subtree: bool = False,
    newest_first: bool = False,
    limit: int = 500,
    offset: int = 0,
    with_urls: bool,
) -> list[PhotoOut]:
    """Фото объекта: по факту (одному или нескольким), по узлу или поддереву; страницами."""
    statement = (
        select(
            *_COLUMNS,
            WorkPhoto.s3_key,
            User.full_name.label("uploaded_by_name"),
            WorkActual.node_id,
            Work.name.label("work_name"),
            WorkActual.work_date,
        )
        .join(WorkActual, WorkActual.id == WorkPhoto.work_actual_id)
        .outerjoin(Work, Work.id == WorkActual.work_id)
        .outerjoin(User, User.id == WorkPhoto.uploaded_by)
        .where(WorkPhoto.project_id == project_id)
    )
    if actual_ids:
        statement = statement.where(WorkPhoto.work_actual_id.in_(actual_ids))
    if node_id is not None:
        if subtree:
            statement = statement.join(ProjectNode, ProjectNode.id == WorkActual.node_id).where(
                ProjectNode.path.contains([node_id])
            )
        else:
            statement = statement.where(WorkActual.node_id == node_id)
    order = (
        (WorkPhoto.created_at.desc(), WorkPhoto.id.desc())
        if newest_first
        else (WorkPhoto.created_at, WorkPhoto.id)
    )
    rows = await session.execute(statement.order_by(*order).limit(limit).offset(offset))
    ttl = get_settings().photo_download_ttl_seconds
    return [
        _out(
            row,
            url=storage.presign_get(row.s3_key, row.content_type, ttl) if with_urls else None,
            ttl=ttl,
        )
        for row in rows
    ]


async def download_url(
    session: AsyncSession, project_id: uuid.UUID, photo_id: uuid.UUID
) -> DownloadUrl:
    """Ссылка на скачивание живёт недолго и выдаётся только при доступе к объекту фото (RLS)."""
    row = (
        await session.execute(
            select(WorkPhoto.s3_key, WorkPhoto.content_type).where(
                WorkPhoto.id == photo_id, WorkPhoto.project_id == project_id
            )
        )
    ).one_or_none()
    if row is None:
        raise not_found()
    ttl = get_settings().photo_download_ttl_seconds
    return DownloadUrl(url=storage.presign_get(row.s3_key, row.content_type, ttl), expires_in=ttl)


async def delete_photo(
    session: AsyncSession, project_id: uuid.UUID, photo_id: uuid.UUID, actor: uuid.UUID
) -> None:
    """Мягкое удаление записи; файл в S3 остаётся. Кто может удалить, решает БД."""
    async with db_rules(session):
        deleted = await session.scalar(
            update(WorkPhoto)
            .where(
                WorkPhoto.id == photo_id,
                WorkPhoto.project_id == project_id,
                WorkPhoto.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(WorkPhoto.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()
