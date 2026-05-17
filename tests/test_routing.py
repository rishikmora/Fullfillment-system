"""
Unit and integration tests for the fulfillment system.

Run with: pytest tests/ -v --cov=app --cov-report=term-missing
"""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.services.routing import route_order, haversine_km
from app.models.models import Warehouse, Inventory, WarehouseStatus
from app.main import app
from app.db.base import get_db


# ─── Unit Tests: Haversine ───────────────────────────────────────────────────

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(12.97, 77.59, 12.97, 77.59) == 0.0

    def test_mumbai_to_delhi(self):
        dist = haversine_km(19.076, 72.877, 28.614, 77.209)
        assert 1100 < dist < 1200, f"Expected ~1150km, got {dist:.0f}km"

    def test_symmetry(self):
        d1 = haversine_km(19.076, 72.877, 28.614, 77.209)
        d2 = haversine_km(28.614, 77.209, 19.076, 72.877)
        assert abs(d1 - d2) < 0.01

    def test_chennai_to_hyderabad(self):
        dist = haversine_km(13.082, 80.270, 17.385, 78.486)
        assert 500 < dist < 600


# ─── Unit Tests: Routing Algorithm ──────────────────────────────────────────

def make_warehouse(id_, name, lat, lon):
    wh = MagicMock(spec=Warehouse)
    wh.id = id_
    wh.name = name
    wh.latitude = lat
    wh.longitude = lon
    wh.status = WarehouseStatus.ACTIVE
    return wh


def make_inventory(warehouse_id, product_id, qty, reserved=0):
    inv = MagicMock(spec=Inventory)
    inv.warehouse_id = warehouse_id
    inv.product_id = product_id
    inv.quantity = qty
    inv.reserved = reserved
    inv.available = qty - reserved
    return inv


class TestRoutingAlgorithm:
    @patch("app.services.routing.get_cached_inventory", return_value=None)
    @patch("app.services.routing.cache_inventory")
    @patch("app.services.routing.decrement_cached_inventory", return_value=50)
    def test_routes_to_nearest_warehouse(self, mock_dec, mock_cache, mock_get):
        dest_lat, dest_lon = 18.5204, 73.8567

        wh_mumbai = make_warehouse("wh-mumbai", "Mumbai Hub", 19.076, 72.877)
        wh_delhi = make_warehouse("wh-delhi", "Delhi Hub", 28.614, 77.209)
        inv_mumbai = make_inventory("wh-mumbai", "prod-1", 100)

        db = MagicMock()

        def query_side(model):
            q = MagicMock()
            if model == Warehouse:
                q.filter.return_value.all.return_value = [wh_mumbai, wh_delhi]
            elif model == Inventory:
                def inv_filter(*args):
                    fq = MagicMock()
                    fq.first.return_value = inv_mumbai
                    fq.with_for_update.return_value.first.return_value = inv_mumbai
                    return fq
                q.filter.side_effect = inv_filter
            return q

        db.query.side_effect = query_side
        mock_get.side_effect = lambda wid, pid: 100

        result = route_order(db, "prod-1", 10, dest_lat, dest_lon)

        assert result.success is True
        assert result.warehouse_id == "wh-mumbai"
        assert result.distance_km < 200

    @patch("app.services.routing.get_cached_inventory", return_value=0)
    def test_fails_when_no_stock(self, mock_get):
        wh = make_warehouse("wh-1", "Hub", 19.076, 72.877)
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [wh]
        result = route_order(db, "prod-1", 10, 18.52, 73.85)
        assert result.success is False
        assert "stock" in result.failure_reason.lower()

    @patch("app.services.routing.get_cached_inventory", return_value=None)
    @patch("app.services.routing.cache_inventory")
    def test_fails_when_no_active_warehouses(self, mock_cache, mock_get):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = route_order(db, "prod-1", 5, 18.52, 73.85)
        assert result.success is False
        assert "No active warehouses" in result.failure_reason


# ─── API Health Test ─────────────────────────────────────────────────────────

class TestAPI:
    def setup_method(self):
        self.mock_db = MagicMock()
        self.mock_db.query.return_value.filter.return_value.first.return_value = None
        app.dependency_overrides[get_db] = lambda: self.mock_db
        self.client = TestClient(app, raise_server_exceptions=False)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_health_check(self):
        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_order_not_found(self):
        response = self.client.get("/orders/nonexistent-id")
        assert response.status_code == 404

    def test_warehouse_not_found(self):
        response = self.client.get("/warehouses/nonexistent-id")
        assert response.status_code == 404
