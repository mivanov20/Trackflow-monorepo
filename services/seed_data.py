from tinydb import TinyDB
from sqlmodel import Session, select

from services.auth import _users_path
from services.database import create_db_and_tables, engine
from services.models import SKU, StockEntry, StockExit

SKU_SEED = [
    {"name": "Classic White Sneaker - Size 42", "sku": "CLT-SNK-W-42", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "LA"},
    {"name": "Classic White Sneaker - Size 42", "sku": "CLT-SNK-W-42-Z", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "ZGZ"},
    {"name": "Wireless Earbuds Pro", "sku": "TEC-EAR-001", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "LA"},
    {"name": "Hydrating Face Serum 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab Cosmetics", "category": "cosmetics", "warehouse": "ZGZ"},
    {"name": "Slim Fit Chino - Navy 32/32", "sku": "CLT-CHN-N-32", "client_name": "UrbanThread", "category": "fashion", "warehouse": "LA"},
    {"name": "USB-C Fast Charger 65W", "sku": "TEC-CHG-065", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "ZGZ"},
]


def seed_development_inventory() -> None:
    """Carga datos de desarrollo después de registrar un usuario operativo real."""
    with TinyDB(_users_path()) as users_db:
        users = users_db.all()
    if not users:
        raise RuntimeError("Register an operative with POST /auth/register before seeding inventory.")
    user_uuid = users[0]["user_uuid"]

    create_db_and_tables()
    with Session(engine) as session:
        existing_by_code = {item.sku: item for item in session.exec(select(SKU)).all()}
        for item in SKU_SEED:
            if item["sku"] not in existing_by_code:
                sku = SKU(**item)
                session.add(sku)
                session.flush()
                existing_by_code[item["sku"]] = sku
        session.commit()

        entries = [
            ("CLT-SNK-W-42", 20, "PO-2024-0098", "LA"),
            ("CLT-SNK-W-42", 5, "GR-LA-0234", "LA"),
            ("CLT-SNK-W-42-Z", 15, "PO-ZGZ-0042", "ZGZ"),
            ("TEC-EAR-001", 12, "GR-LA-0240", "LA"),
            ("CSM-SRM-030", 18, "PO-ZGZ-0104", "ZGZ"),
        ]
        for code, quantity, reference, warehouse in entries:
            exists = session.exec(select(StockEntry).where(StockEntry.reference == reference)).first()
            if exists is None:
                session.add(StockEntry(sku_id=existing_by_code[code].id, quantity=quantity, reference=reference, warehouse=warehouse, user_uuid=user_uuid))

        exits = [
            ("CLT-SNK-W-42", 4, "dispatch", "1Z999AA10123456784", "LA"),
            ("CLT-SNK-W-42", 1, "loss", None, "LA"),
            ("CLT-SNK-W-42-Z", 3, "dispatch", "MRW240000042", "ZGZ"),
        ]
        for code, quantity, exit_type, tracking_number, warehouse in exits:
            exists = session.exec(
                select(StockExit).where(
                    StockExit.sku_id == existing_by_code[code].id,
                    StockExit.exit_type == exit_type,
                    StockExit.tracking_number == tracking_number,
                )
            ).first()
            if exists is None:
                session.add(StockExit(sku_id=existing_by_code[code].id, quantity=quantity, exit_type=exit_type, tracking_number=tracking_number, warehouse=warehouse, user_uuid=user_uuid))
        session.commit()


if __name__ == "__main__":
    seed_development_inventory()
    print("Development inventory seed complete.")