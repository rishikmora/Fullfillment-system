import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import Warehouse, Inventory, Product, WarehouseStatus
from app.api.schemas import (
    WarehouseCreate, WarehouseResponse,
    InventoryUpdateRequest, InventoryResponse,
)
from app.db.redis_client import cache_inventory, invalidate_inventory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/warehouses", tags=["warehouses"])


@router.post("/", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
def create_warehouse(body: WarehouseCreate, db: Session = Depends(get_db)):
    wh = Warehouse(**body.model_dump())
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@router.get("/", response_model=list[WarehouseResponse])
def list_warehouses(db: Session = Depends(get_db)):
    return db.query(Warehouse).filter(Warehouse.status == WarehouseStatus.ACTIVE).all()


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
def get_warehouse(warehouse_id: str, db: Session = Depends(get_db)):
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    return wh


@router.put("/{warehouse_id}/inventory/{product_id}", response_model=InventoryResponse)
def update_inventory(
    warehouse_id: str,
    product_id: str,
    body: InventoryUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Set absolute inventory level for a warehouse-product pair.
    Invalidates Redis cache so next read hits DB (cache-aside pattern).
    """
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found")

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_id == product_id,
        )
        .first()
    )
    if inv:
        inv.quantity = body.quantity
    else:
        inv = Inventory(
            warehouse_id=warehouse_id,
            product_id=product_id,
            quantity=body.quantity,
            reserved=0,
        )
        db.add(inv)

    db.commit()
    db.refresh(inv)

    # Warm Redis with new value immediately
    cache_inventory(warehouse_id, product_id, inv.available)

    return InventoryResponse(
        warehouse_id=inv.warehouse_id,
        product_id=inv.product_id,
        quantity=inv.quantity,
        reserved=inv.reserved,
        available=inv.available,
    )


@router.get("/{warehouse_id}/inventory", response_model=list[InventoryResponse])
def get_inventory(warehouse_id: str, db: Session = Depends(get_db)):
    rows = db.query(Inventory).filter(Inventory.warehouse_id == warehouse_id).all()
    return [
        InventoryResponse(
            warehouse_id=r.warehouse_id,
            product_id=r.product_id,
            quantity=r.quantity,
            reserved=r.reserved,
            available=r.available,
        )
        for r in rows
    ]
