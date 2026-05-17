"""
Warehouse routing algorithm.

Strategy:
  1. Filter warehouses that have sufficient available stock (from Redis cache first,
     fallback to DB).
  2. Among eligible warehouses, pick the closest one to the delivery destination
     using the Haversine formula (great-circle distance).
  3. If no warehouse has stock, return a FAILED result.
  4. After assignment, atomically decrement Redis cache and write to DB.

Why Haversine?
  At Amazon scale, proximity reduces last-mile delivery cost. The greedy approach
  (nearest eligible warehouse) is O(n) per order and good enough for single-item
  routing. Multi-item orders with split-shipment logic would use a more complex
  optimizer (LP/ILP) — that's a natural interview talking point.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Warehouse, Inventory, WarehouseStatus
from app.db.redis_client import (
    get_cached_inventory,
    cache_inventory,
    decrement_cached_inventory,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RoutingResult:
    success: bool
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    distance_km: Optional[float] = None
    failure_reason: Optional[str] = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Returns great-circle distance in km between two (lat, lon) points.
    Uses Earth radius = 6371 km.
    """
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _get_available_stock(db: Session, warehouse_id: str, product_id: str) -> int:
    """
    Read available stock. Redis is the source of truth for live availability.
    On cache miss, read from DB and warm the cache.
    """
    cached = get_cached_inventory(warehouse_id, product_id)
    if cached is not None:
        return cached

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_id == product_id,
        )
        .first()
    )
    available = inv.available if inv else 0
    cache_inventory(warehouse_id, product_id, available)
    return available


def route_order(
    db: Session,
    product_id: str,
    quantity: int,
    dest_lat: float,
    dest_lon: float,
) -> RoutingResult:
    """
    Main routing entry point.

    Returns the best warehouse for this order, or a failure result.
    Thread-safe: Redis DECRBY is atomic; DB update uses row-level locking.
    """
    warehouses = (
        db.query(Warehouse)
        .filter(Warehouse.status == WarehouseStatus.ACTIVE)
        .all()
    )

    if not warehouses:
        return RoutingResult(success=False, failure_reason="No active warehouses")

    # Score each warehouse: (distance, warehouse) — skip those with insufficient stock
    candidates = []
    for wh in warehouses:
        available = _get_available_stock(db, wh.id, product_id)
        if available >= quantity:
            dist = haversine_km(wh.latitude, wh.longitude, dest_lat, dest_lon)
            candidates.append((dist, wh))

    if not candidates:
        return RoutingResult(
            success=False,
            failure_reason=f"Insufficient stock across all warehouses for product {product_id}",
        )

    # Pick nearest
    candidates.sort(key=lambda x: x[0])
    distance, best_wh = candidates[0]

    # Atomically decrement Redis cache
    new_cached = decrement_cached_inventory(best_wh.id, product_id, quantity)
    if new_cached is not None and new_cached < 0:
        # Race condition detected — another request took the last units
        logger.warning(
            "Inventory race condition on warehouse=%s product=%s, re-routing",
            best_wh.id, product_id,
        )
        # Re-run without this warehouse (simple fallback)
        remaining = [(d, wh) for d, wh in candidates[1:]]
        if not remaining:
            return RoutingResult(success=False, failure_reason="Stock exhausted during routing")
        distance, best_wh = remaining[0]

    # Persist to DB with pessimistic lock on inventory row
    inv = (
        db.query(Inventory)
        .filter(
            Inventory.warehouse_id == best_wh.id,
            Inventory.product_id == product_id,
        )
        .with_for_update()  # row-level lock
        .first()
    )
    if not inv or inv.available < quantity:
        return RoutingResult(success=False, failure_reason="DB stock validation failed")

    inv.reserved += quantity
    db.flush()

    logger.info(
        "Routed order to warehouse=%s distance=%.1fkm remaining_stock=%d",
        best_wh.name, distance, inv.available,
    )

    return RoutingResult(
        success=True,
        warehouse_id=best_wh.id,
        warehouse_name=best_wh.name,
        distance_km=round(distance, 2),
    )
