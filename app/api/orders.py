import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.base import get_db
from app.models.models import Order, OrderStatus, Warehouse, WarehouseStatus
from app.api.schemas import PlaceOrderRequest, OrderResponse, MetricsDashboard
from app.services.order_service import process_order
from app.db.redis_client import get_cached_metrics, cache_metrics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order(
    body: PlaceOrderRequest,
    db: Session = Depends(get_db),
):
    """
    Place a new order. The router:
      1. Creates the order record (PENDING)
      2. Runs the warehouse routing algorithm
      3. Publishes a Kafka event
      4. Returns the result synchronously

    In a full production system, step 2 would be async via a Kafka consumer
    worker — the HTTP response would return PENDING and the client would poll.
    For this demo, routing is synchronous for simplicity.
    """
    order = Order(
        customer_id=body.customer_id,
        product_id=body.product_id,
        quantity=body.quantity,
        destination_lat=body.destination_lat,
        destination_lon=body.destination_lon,
        status=OrderStatus.PENDING,
    )
    db.add(order)
    db.flush()  # get the ID without committing

    result = await process_order(
        db=db,
        order_id=order.id,
        customer_id=body.customer_id,
        product_id=body.product_id,
        quantity=body.quantity,
        dest_lat=body.destination_lat,
        dest_lon=body.destination_lon,
    )

    db.refresh(order)

    return OrderResponse(
        order_id=order.id,
        status=order.status,
        warehouse=result.get("warehouse"),
        distance_km=result.get("distance_km"),
        failure_reason=result.get("reason"),
        created_at=order.created_at,
    )


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return OrderResponse(
        order_id=order.id,
        status=order.status,
        warehouse=order.warehouse.name if order.warehouse else None,
        distance_km=None,
        failure_reason=order.failure_reason,
        created_at=order.created_at,
    )


@router.get("/metrics/dashboard", response_model=MetricsDashboard)
def get_metrics(db: Session = Depends(get_db)):
    """
    Real-time dashboard metrics. Cached in Redis for 10s to avoid
    repeated aggregate queries under high load.
    """
    cached = get_cached_metrics()
    if cached:
        return MetricsDashboard(**cached)

    total = db.query(func.count(Order.id)).scalar() or 0
    dispatched = (
        db.query(func.count(Order.id))
        .filter(Order.status == OrderStatus.DISPATCHED)
        .scalar() or 0
    )
    failed = (
        db.query(func.count(Order.id))
        .filter(Order.status == OrderStatus.FAILED)
        .scalar() or 0
    )
    active_warehouses = (
        db.query(func.count(Warehouse.id))
        .filter(Warehouse.status == WarehouseStatus.ACTIVE)
        .scalar() or 0
    )

    fulfillment_rate = round((dispatched / total * 100) if total > 0 else 0.0, 2)

    metrics = {
        "total_orders": total,
        "dispatched": dispatched,
        "failed": failed,
        "fulfillment_rate_pct": fulfillment_rate,
        "avg_distance_km": None,
        "active_warehouses": active_warehouses,
    }
    cache_metrics(metrics)
    return MetricsDashboard(**metrics)
