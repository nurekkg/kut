import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.rbac.enums import Action, Module
from app.modules.stock import service
from app.modules.stock.schemas import (
    BalanceOut,
    MovementOut,
    PostOut,
    ReverseOut,
    WarehouseIn,
    WarehouseOut,
)

router = APIRouter(tags=["stock"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[Principal, Depends(internal_only(require(Module.MATERIALS, Action.READ)))]
CatalogWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.MATERIALS, Action.WRITE, qualifier="catalog"))),
]
StockWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.MATERIALS, Action.WRITE, qualifier="stock"))),
]


@router.get("/warehouses", response_model=list[WarehouseOut])
async def list_warehouses(
    session: Db, _principal: Reader, project_id: uuid.UUID | None = None
) -> list[WarehouseOut]:
    rows = await service.list_warehouses(session, project_id)
    return [WarehouseOut(id=r.id, project_id=r.project_id, name=r.name, kind=r.kind) for r in rows]


@router.post("/warehouses", status_code=status.HTTP_201_CREATED, response_model=WarehouseOut)
async def create_warehouse(
    body: WarehouseIn, session: Db, _principal: CatalogWriter
) -> WarehouseOut:
    row = await service.create_warehouse(session, body.project_id, body.name)
    return WarehouseOut(id=row.id, project_id=row.project_id, name=row.name, kind=row.kind)


@router.get("/stock/balances", response_model=list[BalanceOut])
async def balances(
    session: Db,
    _principal: Reader,
    warehouse_id: uuid.UUID,
    by_batch: bool = Query(default=False),
) -> list[BalanceOut]:
    rows = await service.balances(session, warehouse_id, by_batch)
    return [
        BalanceOut(
            warehouse_id=r.warehouse_id,
            material_id=r.material_id,
            base_unit=r.base_unit,
            qty=r.qty,
            batch_id=r.batch_id,
            batch_no=r.batch_no,
        )
        for r in rows
    ]


@router.get("/stock/movements", response_model=list[MovementOut])
async def movements(
    session: Db,
    _principal: Reader,
    warehouse_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MovementOut]:
    rows = await service.movements(session, warehouse_id, limit)
    return [MovementOut.model_validate(row, from_attributes=True) for row in rows]


@router.post(
    "/stock/waybills/{document_id}/versions/{version_id}/post",
    response_model=PostOut,
)
async def post_waybill(
    document_id: uuid.UUID, version_id: uuid.UUID, session: Db, _principal: StockWriter
) -> PostOut:
    """Проведение внутренней накладной (ВН): расход сразу, без маршрута согласования."""
    return PostOut(movements=await service.post_waybill(session, document_id, version_id))


@router.post("/stock/movements/{movement_id}/reverse", response_model=ReverseOut)
async def reverse(movement_id: uuid.UUID, session: Db, _principal: StockWriter) -> ReverseOut:
    return ReverseOut(movement_id=await service.reverse(session, movement_id))
