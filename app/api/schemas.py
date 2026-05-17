from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum


class OrderStatusEnum(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    DISPATCHED = "dispatched"
    FAILED = "failed"


class PlaceOrderRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, max_length=100)
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1, le=10_000)
    destination_lat: float = Field(..., ge=-90, le=90)
    destination_lon: float = Field(..., ge=-180, le=180)

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v


class OrderResponse(BaseModel):
    order_id: str
    status: OrderStatusEnum
    warehouse: Optional[str] = None
    distance_km: Optional[float] = None
    failure_reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WarehouseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    city: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class WarehouseResponse(BaseModel):
    id: str
    name: str
    city: str
    latitude: float
    longitude: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class InventoryUpdateRequest(BaseModel):
    quantity: int = Field(..., ge=0)


class InventoryResponse(BaseModel):
    warehouse_id: str
    product_id: str
    quantity: int
    reserved: int
    available: int

    model_config = {"from_attributes": True}


class MetricsDashboard(BaseModel):
    total_orders: int
    dispatched: int
    failed: int
    fulfillment_rate_pct: float
    avg_distance_km: Optional[float]
    active_warehouses: int
