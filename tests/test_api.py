"""
Tests for app/api/warehouses.py and app/api/orders.py
Uses real SQLite in-memory DB via the `client` fixture.
"""

from unittest.mock import patch, AsyncMock


class TestWarehouseAPI:
    def test_create_warehouse_returns_201(self, client):
        resp = client.post("/warehouses/", json={
            "name": "Test Hub",
            "city": "Pune",
            "latitude": 18.5204,
            "longitude": 73.8567,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Hub"
        assert data["city"] == "Pune"
        assert "id" in data
        assert data["status"] == "active"

    def test_create_warehouse_validates_latitude(self, client):
        resp = client.post("/warehouses/", json={
            "name": "Bad Hub",
            "city": "Nowhere",
            "latitude": 999,  # invalid
            "longitude": 73.8,
        })
        assert resp.status_code == 422

    def test_list_warehouses_returns_active_only(self, client, warehouse_mumbai, warehouse_inactive):
        resp = client.get("/warehouses/")
        assert resp.status_code == 200
        names = [w["name"] for w in resp.json()]
        assert "Mumbai Central Hub" in names
        assert "Closed Hub" not in names

    def test_get_warehouse_by_id(self, client, warehouse_mumbai):
        resp = client.get(f"/warehouses/{warehouse_mumbai.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == warehouse_mumbai.id

    def test_get_warehouse_not_found(self, client):
        resp = client.get("/warehouses/does-not-exist")
        assert resp.status_code == 404

    def test_update_inventory(self, client, warehouse_mumbai, product):
        with patch("app.api.warehouses.cache_inventory"):
            resp = client.put(
                f"/warehouses/{warehouse_mumbai.id}/inventory/{product.id}",
                json={"quantity": 200},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quantity"] == 200
        assert data["available"] == 200

    def test_update_inventory_warehouse_not_found(self, client, product):
        resp = client.put(
            f"/warehouses/bad-id/inventory/{product.id}",
            json={"quantity": 50},
        )
        assert resp.status_code == 404

    def test_update_inventory_product_not_found(self, client, warehouse_mumbai):
        resp = client.put(
            f"/warehouses/{warehouse_mumbai.id}/inventory/bad-product-id",
            json={"quantity": 50},
        )
        assert resp.status_code == 404

    def test_get_inventory_returns_list(self, client, warehouse_mumbai, inventory_mumbai):
        resp = client.get(f"/warehouses/{warehouse_mumbai.id}/inventory")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) >= 1
        row = rows[0]
        assert "quantity" in row
        assert "reserved" in row
        assert "available" in row
        assert row["available"] == row["quantity"] - row["reserved"]


class TestOrdersAPI:
    def test_place_order_dispatched(self, client, product, warehouse_mumbai, inventory_mumbai):
        with patch("app.services.order_service.route_order") as mock_route, \
             patch("app.services.order_service.publish_order_event", new_callable=AsyncMock), \
             patch("app.services.order_service.publish_reorder_event", new_callable=AsyncMock), \
             patch("app.services.order_service.cache_inventory"):
            from app.services.routing import RoutingResult
            mock_route.return_value = RoutingResult(
                success=True,
                warehouse_id=warehouse_mumbai.id,
                warehouse_name=warehouse_mumbai.name,
                distance_km=148.3,
            )
            resp = client.post("/orders/", json={
                "customer_id": "cust-001",
                "product_id": product.id,
                "quantity": 2,
                "destination_lat": 18.5204,
                "destination_lon": 73.8567,
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "dispatched"
        assert data["warehouse"] == "Mumbai Central Hub"
        assert data["distance_km"] == 148.3
        assert "order_id" in data

    def test_place_order_failed_no_stock(self, client, product):
        with patch("app.services.order_service.route_order") as mock_route, \
             patch("app.services.order_service.publish_order_event", new_callable=AsyncMock), \
             patch("app.services.order_service.publish_reorder_event", new_callable=AsyncMock):
            from app.services.routing import RoutingResult
            mock_route.return_value = RoutingResult(
                success=False,
                failure_reason="Insufficient stock across all warehouses",
            )
            resp = client.post("/orders/", json={
                "customer_id": "cust-001",
                "product_id": product.id,
                "quantity": 9999,
                "destination_lat": 18.5204,
                "destination_lon": 73.8567,
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "failed"

    def test_place_order_invalid_quantity(self, client, product):
        resp = client.post("/orders/", json={
            "customer_id": "cust-001",
            "product_id": product.id,
            "quantity": 0,  # must be >= 1
            "destination_lat": 18.5204,
            "destination_lon": 73.8567,
        })
        assert resp.status_code == 422

    def test_place_order_invalid_coordinates(self, client, product):
        resp = client.post("/orders/", json={
            "customer_id": "cust-001",
            "product_id": product.id,
            "quantity": 1,
            "destination_lat": 999,   # invalid
            "destination_lon": 73.8567,
        })
        assert resp.status_code == 422

    def test_get_order_by_id(self, client, order_pending, warehouse_mumbai):
        resp = client.get(f"/orders/{order_pending.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"] == order_pending.id
        assert data["status"] == "pending"

    def test_get_order_not_found(self, client):
        resp = client.get("/orders/nonexistent-id")
        assert resp.status_code == 404

    def test_metrics_dashboard(self, client):
        with patch("app.api.orders.get_cached_metrics", return_value=None), \
             patch("app.api.orders.cache_metrics"):
            resp = client.get("/orders/metrics/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_orders" in data
        assert "fulfillment_rate_pct" in data
        assert "active_warehouses" in data

    def test_metrics_served_from_cache(self, client):
        cached = {
            "total_orders": 500,
            "dispatched": 490,
            "failed": 10,
            "fulfillment_rate_pct": 98.0,
            "avg_distance_km": None,
            "active_warehouses": 5,
        }
        with patch("app.api.orders.get_cached_metrics", return_value=cached):
            resp = client.get("/orders/metrics/dashboard")
        assert resp.status_code == 200
        assert resp.json()["total_orders"] == 500
