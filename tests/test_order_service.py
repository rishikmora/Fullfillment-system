"""
Tests for app/services/order_service.py
Covers: full order lifecycle, reorder triggers, Kafka publishing.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.order_service import process_order, _check_and_trigger_reorder
from app.models.models import OrderStatus


class TestProcessOrder:
    @pytest.mark.asyncio
    async def test_successful_order_dispatches_and_publishes(
        self, db, order_pending, product, warehouse_mumbai, inventory_mumbai, mock_publish
    ):
        pub_order, pub_reorder = mock_publish

        with patch("app.services.order_service.route_order") as mock_route, \
             patch("app.services.order_service.cache_inventory"):
            from app.services.routing import RoutingResult
            mock_route.return_value = RoutingResult(
                success=True,
                warehouse_id=warehouse_mumbai.id,
                warehouse_name=warehouse_mumbai.name,
                distance_km=148.3,
            )

            result = await process_order(
                db=db,
                order_id=order_pending.id,
                customer_id=order_pending.customer_id,
                product_id=product.id,
                quantity=5,
                dest_lat=18.5204,
                dest_lon=73.8567,
            )

        assert result["status"] == "dispatched"
        assert result["warehouse"] == warehouse_mumbai.name
        assert result["distance_km"] == 148.3
        pub_order.assert_called_once()

        # Verify event payload
        call_args = pub_order.call_args[0][0]
        assert call_args["event_type"] == "order_dispatched"
        assert call_args["order_id"] == order_pending.id
        assert call_args["warehouse_id"] == warehouse_mumbai.id

        # Verify DB updated
        db.refresh(order_pending)
        assert order_pending.status == OrderStatus.DISPATCHED
        assert order_pending.warehouse_id == warehouse_mumbai.id
        assert order_pending.dispatched_at is not None

    @pytest.mark.asyncio
    async def test_failed_order_updates_status_and_publishes(
        self, db, order_pending, product, mock_publish
    ):
        pub_order, _ = mock_publish

        with patch("app.services.order_service.route_order") as mock_route:
            from app.services.routing import RoutingResult
            mock_route.return_value = RoutingResult(
                success=False,
                failure_reason="Insufficient stock across all warehouses",
            )

            result = await process_order(
                db=db,
                order_id=order_pending.id,
                customer_id=order_pending.customer_id,
                product_id=product.id,
                quantity=999,
                dest_lat=18.5204,
                dest_lon=73.8567,
            )

        assert result["status"] == "failed"
        assert "stock" in result["reason"].lower()
        pub_order.assert_called_once()

        call_args = pub_order.call_args[0][0]
        assert call_args["event_type"] == "order_failed"

        db.refresh(order_pending)
        assert order_pending.status == OrderStatus.FAILED
        assert order_pending.failure_reason is not None

    @pytest.mark.asyncio
    async def test_raises_on_nonexistent_order(self, db, product):
        with pytest.raises(ValueError, match="not found"):
            await process_order(
                db=db,
                order_id="nonexistent-uuid",
                customer_id="cust-1",
                product_id=product.id,
                quantity=1,
                dest_lat=18.5,
                dest_lon=73.8,
            )


class TestReorderTrigger:
    @pytest.mark.asyncio
    async def test_triggers_reorder_when_below_threshold(
        self, db, warehouse_mumbai, product, mock_publish
    ):
        _, pub_reorder = mock_publish

        # Set inventory just at threshold
        from app.models.models import Inventory
        inv = Inventory(
            warehouse_id=warehouse_mumbai.id,
            product_id=product.id,
            quantity=8,
            reserved=0,
        )
        db.add(inv)
        db.commit()

        with patch("app.services.order_service.cache_inventory"):
            await _check_and_trigger_reorder(db, warehouse_mumbai.id, product.id)

        pub_reorder.assert_called_once()
        call_args = pub_reorder.call_args[0][0]
        assert call_args["event_type"] == "reorder_triggered"
        assert call_args["warehouse_id"] == warehouse_mumbai.id
        assert call_args["current_stock"] == 8

    @pytest.mark.asyncio
    async def test_no_reorder_when_stock_sufficient(
        self, db, warehouse_mumbai, product, mock_publish
    ):
        _, pub_reorder = mock_publish

        from app.models.models import Inventory
        inv = Inventory(
            warehouse_id=warehouse_mumbai.id,
            product_id=product.id,
            quantity=100,
            reserved=0,
        )
        db.add(inv)
        db.commit()

        with patch("app.services.order_service.cache_inventory"):
            await _check_and_trigger_reorder(db, warehouse_mumbai.id, product.id)

        pub_reorder.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_reorder_when_inventory_missing(
        self, db, warehouse_mumbai, product, mock_publish
    ):
        _, pub_reorder = mock_publish
        # No inventory row inserted
        with patch("app.services.order_service.cache_inventory"):
            await _check_and_trigger_reorder(db, warehouse_mumbai.id, product.id)
        pub_reorder.assert_not_called()
