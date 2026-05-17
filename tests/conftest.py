"""
Shared pytest fixtures for the fulfillment system test suite.
All external dependencies (Redis, Kafka, DB) are mocked so tests run
without any running infrastructure.
"""

import pytest
import uuid
import tempfile
import os
from unittest.mock import MagicMock, AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base, get_db
from app.main import app
from app.models.models import (
    Warehouse, Product, Inventory, Order,
    WarehouseStatus, OrderStatus,
)
from fastapi.testclient import TestClient


# ── SQLite file-based DB (avoids in-memory connection isolation issues) ────────

@pytest.fixture(scope="function")
def db_engine():
    import app.db.base as db_module
    db_file = tempfile.mktemp(suffix=".db")
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=test_engine)
    original_engine = db_module.engine
    db_module.engine = test_engine
    yield test_engine
    db_module.engine = original_engine
    test_engine.dispose()
    try:
        os.unlink(db_file)
    except Exception:
        pass


@pytest.fixture(scope="function")
def db(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# ── Domain objects ────────────────────────────────────────────────────────────

@pytest.fixture
def warehouse_mumbai(db):
    wh = Warehouse(
        id=str(uuid.uuid4()),
        name="Mumbai Central Hub",
        city="Mumbai",
        latitude=19.0760,
        longitude=72.8777,
        status=WarehouseStatus.ACTIVE,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@pytest.fixture
def warehouse_delhi(db):
    wh = Warehouse(
        id=str(uuid.uuid4()),
        name="Delhi NCR Hub",
        city="Delhi",
        latitude=28.6139,
        longitude=77.2090,
        status=WarehouseStatus.ACTIVE,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@pytest.fixture
def warehouse_inactive(db):
    wh = Warehouse(
        id=str(uuid.uuid4()),
        name="Closed Hub",
        city="Nagpur",
        latitude=21.1458,
        longitude=79.0882,
        status=WarehouseStatus.INACTIVE,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@pytest.fixture
def product(db):
    p = Product(
        id=str(uuid.uuid4()),
        sku="PHONE-TEST-001",
        name="Test Smartphone",
        weight_kg=0.4,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def inventory_mumbai(db, warehouse_mumbai, product):
    inv = Inventory(
        id=str(uuid.uuid4()),
        warehouse_id=warehouse_mumbai.id,
        product_id=product.id,
        quantity=100,
        reserved=10,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@pytest.fixture
def inventory_delhi(db, warehouse_delhi, product):
    inv = Inventory(
        id=str(uuid.uuid4()),
        warehouse_id=warehouse_delhi.id,
        product_id=product.id,
        quantity=50,
        reserved=0,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@pytest.fixture
def order_pending(db, product, warehouse_mumbai):
    o = Order(
        id=str(uuid.uuid4()),
        customer_id="cust-test-001",
        product_id=product.id,
        quantity=5,
        destination_lat=18.5204,
        destination_lon=73.8567,
        status=OrderStatus.PENDING,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


# ── Mocked Redis ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    with patch("app.db.redis_client._redis_client") as mock_r:
        mock_r.get.return_value = None
        mock_r.setex.return_value = True
        mock_r.delete.return_value = 1
        mock_r.exists.return_value = 0
        mock_r.decrby.return_value = 90
        yield mock_r


@pytest.fixture
def mock_redis_with_stock():
    with patch("app.db.redis_client.get_redis") as mock_get:
        r = MagicMock()
        r.get.return_value = "100"
        r.setex.return_value = True
        r.exists.return_value = 1
        r.decrby.return_value = 95
        r.delete.return_value = 1
        mock_get.return_value = r
        yield r


# ── Mocked Kafka ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_kafka():
    with patch("app.core.kafka_client._producer") as mock_p:
        mock_p.send_and_wait = AsyncMock(return_value=None)
        yield mock_p


@pytest.fixture
def mock_publish():
    with patch("app.services.order_service.publish_order_event", new_callable=AsyncMock) as pub_order, \
         patch("app.services.order_service.publish_reorder_event", new_callable=AsyncMock) as pub_reorder:
        yield pub_order, pub_reorder


# ── FastAPI test client with full DB + lifespan override ──────────────────────

@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with patch("app.main.Base.metadata.create_all"), \
         patch("app.main.get_producer", new_callable=AsyncMock), \
         patch("app.main.stop_producer", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.clear()
