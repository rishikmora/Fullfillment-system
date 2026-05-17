from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Enum, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

from app.db.base import Base


def gen_uuid():
    return str(uuid.uuid4())


class WarehouseStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    DISPATCHED = "dispatched"
    FAILED = "failed"


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False)
    city = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    status = Column(Enum(WarehouseStatus), default=WarehouseStatus.ACTIVE)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    inventory = relationship("Inventory", back_populates="warehouse", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="warehouse")


class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=gen_uuid)
    sku = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    weight_kg = Column(Float, default=0.5)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    inventory = relationship("Inventory", back_populates="product")


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(String, primary_key=True, default=gen_uuid)
    warehouse_id = Column(String, ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=0)
    reserved = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    warehouse = relationship("Warehouse", back_populates="inventory")
    product = relationship("Product", back_populates="inventory")

    @property
    def available(self) -> int:
        return max(0, self.quantity - self.reserved)


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=gen_uuid)
    customer_id = Column(String(100), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    destination_lat = Column(Float, nullable=False)
    destination_lon = Column(Float, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    warehouse_id = Column(String, ForeignKey("warehouses.id"), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    dispatched_at = Column(DateTime(timezone=True), nullable=True)

    warehouse = relationship("Warehouse", back_populates="orders")
