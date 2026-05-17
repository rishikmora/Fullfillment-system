import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.models import Order, OrderStatus, Inventory
from app.services.routing import route_order
from app.core.kafka_client import publish_order_event, publish_reorder_event
from app.db.redis_client import cache_inventory
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def process_order(
    db: Session,
    order_id: str,
    customer_id: str,
    product_id: str,
    quantity: int,
    dest_lat: float,
    dest_lon: float,
) -> dict:
    """
    Full order lifecycle:
      1. Route to best warehouse
      2. Update order record in DB
      3. Publish Kafka event (for downstream consumers: dispatch, analytics, reorder)
      4. Check low-stock threshold and trigger reorder if needed
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError(f"Order {order_id} not found")

    result = route_order(db, product_id, quantity, dest_lat, dest_lon)

    if result.success:
        order.status = OrderStatus.DISPATCHED
        order.warehouse_id = result.warehouse_id
        order.dispatched_at = datetime.now(timezone.utc)
        db.commit()

        event = {
            "event_type": "order_dispatched",
            "order_id": order_id,
            "customer_id": customer_id,
            "product_id": product_id,
            "quantity": quantity,
            "warehouse_id": result.warehouse_id,
            "warehouse_name": result.warehouse_name,
            "distance_km": result.distance_km,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await publish_order_event(event)

        # Check if we need to trigger a reorder
        await _check_and_trigger_reorder(db, result.warehouse_id, product_id)

        return {
            "status": "dispatched",
            "warehouse": result.warehouse_name,
            "distance_km": result.distance_km,
        }
    else:
        order.status = OrderStatus.FAILED
        order.failure_reason = result.failure_reason
        db.commit()

        await publish_order_event({
            "event_type": "order_failed",
            "order_id": order_id,
            "reason": result.failure_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {
            "status": "failed",
            "reason": result.failure_reason,
        }


async def _check_and_trigger_reorder(
    db: Session, warehouse_id: str, product_id: str
) -> None:
    """
    After fulfilling an order, checks if remaining stock has dropped below the
    low-stock threshold. If so, publishes a reorder event and refreshes the cache.
    """
    inv = (
        db.query(Inventory)
        .filter(
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_id == product_id,
        )
        .first()
    )
    if not inv:
        return

    # Refresh Redis with latest DB value after the order
    cache_inventory(warehouse_id, product_id, inv.available)

    if inv.available <= settings.low_stock_threshold:
        logger.warning(
            "Low stock alert: warehouse=%s product=%s available=%d",
            warehouse_id, product_id, inv.available,
        )
        await publish_reorder_event({
            "event_type": "reorder_triggered",
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "current_stock": inv.available,
            "threshold": settings.low_stock_threshold,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
