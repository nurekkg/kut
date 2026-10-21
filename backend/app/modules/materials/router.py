import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.equipment import imports as spreadsheet
from app.modules.materials import service
from app.modules.materials.schemas import (
    BatchIn,
    BatchOut,
    ImportReport,
    MaterialIn,
    MaterialOut,
    MaterialPatch,
    PriceIn,
    UnitIn,
)
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/materials", tags=["materials"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[Principal, Depends(internal_only(require(Module.MATERIALS, Action.READ)))]
CatalogWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.MATERIALS, Action.WRITE, qualifier="catalog"))),
]


async def _body(request: Request) -> bytes:
    """Файл импорта - тело запроса, как у импорта техники (без multipart)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > spreadsheet.MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "material.import_too_large"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("", response_model=list[MaterialOut])
async def list_materials(
    session: Db,
    _principal: Reader,
    legal_entity_id: uuid.UUID | None = None,
    active: bool = Query(default=False),
) -> list[MaterialOut]:
    return await service.list_materials(session, legal_entity_id, active)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MaterialOut)
async def create_material(body: MaterialIn, session: Db, _principal: CatalogWriter) -> MaterialOut:
    return await service.create_material(session, body)


@router.post("/import", response_model=ImportReport)
async def import_materials(
    request: Request,
    session: Db,
    _principal: CatalogWriter,
    legal_entity_id: uuid.UUID,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> ImportReport:
    """Импорт справочника из XLSX или CSV: всё или ничего, ошибки - по номерам строк."""
    return await service.import_materials(session, legal_entity_id, filename, await _body(request))


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(material_id: uuid.UUID, session: Db, _principal: Reader) -> MaterialOut:
    return await service.get_material(session, material_id)


@router.patch("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: uuid.UUID, body: MaterialPatch, session: Db, _principal: CatalogWriter
) -> MaterialOut:
    return await service.update_material(session, material_id, body)


@router.post(
    "/{material_id}/units", status_code=status.HTTP_201_CREATED, response_model=MaterialOut
)
async def add_unit(
    material_id: uuid.UUID, body: UnitIn, session: Db, _principal: CatalogWriter
) -> MaterialOut:
    return await service.add_unit(session, material_id, body)


@router.post("/{material_id}/batches", status_code=status.HTTP_201_CREATED, response_model=BatchOut)
async def add_batch(
    material_id: uuid.UUID, body: BatchIn, session: Db, _principal: CatalogWriter
) -> BatchOut:
    return await service.add_batch(session, material_id, body)


@router.put("/{material_id}/price", response_model=MaterialOut)
async def set_price(
    material_id: uuid.UUID, body: PriceIn, session: Db, _principal: CatalogWriter
) -> MaterialOut:
    """Цена плана: право на цены закупок проверяет БД (без него - отказ 403)."""
    return await service.set_price(session, material_id, body)
