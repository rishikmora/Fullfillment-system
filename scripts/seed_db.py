"""
Seed the database with realistic Indian warehouses and products.
Run with: python -m scripts.seed_db
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.base import SessionLocal, engine, Base
from app.models.models import Warehouse, Product, Inventory, WarehouseStatus
from app.db.redis_client import cache_inventory

Base.metadata.create_all(bind=engine)

WAREHOUSES = [
    {"name": "Mumbai Central Hub", "city": "Mumbai", "latitude": 19.0760, "longitude": 72.8777},
    {"name": "Delhi NCR Fulfillment", "city": "Delhi", "latitude": 28.6139, "longitude": 77.2090},
    {"name": "Bengaluru South DC", "city": "Bengaluru", "latitude": 12.9716, "longitude": 77.5946},
    {"name": "Chennai Port Hub", "city": "Chennai", "latitude": 13.0827, "longitude": 80.2707},
    {"name": "Hyderabad Express", "city": "Hyderabad", "latitude": 17.3850, "longitude": 78.4867},
]

PRODUCTS = [
    {"sku": "PHONE-001", "name": "Smartphone Pro X", "weight_kg": 0.4},
    {"sku": "LAPTOP-001", "name": "UltraBook 15", "weight_kg": 1.8},
    {"sku": "HDPHONE-001", "name": "Wireless Headphones", "weight_kg": 0.3},
    {"sku": "TABLET-001", "name": "Tab Ultra 12", "weight_kg": 0.6},
    {"sku": "WATCH-001", "name": "SmartWatch Series 5", "weight_kg": 0.1},
]


def seed():
    db = SessionLocal()
    try:
        # Create warehouses
        warehouses = []
        for wh_data in WAREHOUSES:
            existing = db.query(Warehouse).filter(Warehouse.name == wh_data["name"]).first()
            if not existing:
                wh = Warehouse(**wh_data, status=WarehouseStatus.ACTIVE)
                db.add(wh)
                warehouses.append(wh)
            else:
                warehouses.append(existing)
        db.flush()

        # Create products
        products = []
        for p_data in PRODUCTS:
            existing = db.query(Product).filter(Product.sku == p_data["sku"]).first()
            if not existing:
                p = Product(**p_data)
                db.add(p)
                products.append(p)
            else:
                products.append(existing)
        db.flush()

        # Seed inventory: each warehouse gets between 20-200 units of each product
        import random
        random.seed(42)
        for wh in warehouses:
            for product in products:
                qty = random.randint(20, 200)
                existing_inv = db.query(Inventory).filter(
                    Inventory.warehouse_id == wh.id,
                    Inventory.product_id == product.id,
                ).first()
                if not existing_inv:
                    inv = Inventory(
                        warehouse_id=wh.id,
                        product_id=product.id,
                        quantity=qty,
                        reserved=0,
                    )
                    db.add(inv)
                    db.flush()
                    # Warm Redis cache
                    cache_inventory(wh.id, product.id, qty)

        db.commit()
        print(f"Seeded {len(warehouses)} warehouses, {len(products)} products with inventory.")
    except Exception as e:
        db.rollback()
        print(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
