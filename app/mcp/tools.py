"""MCP tool definitions aligned with document categories."""
from app.core.database import SessionLocal
from app.modules.knowledge.infrastructure.milvus import milvus_service


def _search(keyword: str, top_k: int, brand: str | None = None, category: str | None = None):
    where = {}
    if brand: where["brand"] = brand
    if category: where["category"] = category
    return milvus_service.search(keyword, top_k=top_k, where_filter=where or None)


def search_plc_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "plc-manual")


def search_hardware_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "hardware-manual")


def search_driver_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "driver-manual")


def search_hmi_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "hmi-manual")


def search_robot_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "robot-manual")


def search_software_manual(brand: str, keyword: str, top_k: int = 5):
    return _search(keyword, top_k, brand, "software-manual")


def search_best_practice(topic: str, top_k: int = 5):
    return _search(topic, top_k, category="best-practice")


def search_electrical_standard(standard_name: str | None = None, keyword: str | None = None, top_k: int = 5):
    query = " ".join(x for x in [standard_name, keyword] if x)
    return _search(query, top_k, category="electrical-standard")


def get_brand_mapping(source_brand: str, target_brand: str, item: str):
    """Query cross-brand PLC address/instruction mappings from the database."""
    from sqlalchemy import or_

    from app.models.brand_mapping import BrandMapping

    db = SessionLocal()
    try:
        q = db.query(BrandMapping).filter(
            BrandMapping.source_brand == source_brand.lower(),
            BrandMapping.target_brand == target_brand.lower(),
        )
        if item:
            kw = f"%{item}%"
            q = q.filter(
                or_(
                    BrandMapping.item_type.ilike(kw),
                    BrandMapping.source_value.ilike(kw),
                    BrandMapping.target_value.ilike(kw),
                    BrandMapping.description.ilike(kw),
                )
            )
        rows = q.order_by(BrandMapping.item_type, BrandMapping.id).all()
        mappings = [
            {
                "item_type": r.item_type,
                "source_value": r.source_value,
                "target_value": r.target_value,
                "description": r.description,
            }
            for r in rows
        ]
        return {
            "source_brand": source_brand,
            "target_brand": target_brand,
            "item": item,
            "mappings": mappings,
        }
    finally:
        db.close()


def list_available_documents(brand: str | None = None, category: str | None = None):
    """List available documents from the database, filtered by brand/category."""
    from app.models.document import Document

    db = SessionLocal()
    try:
        q = db.query(Document)
        if brand:
            q = q.filter(Document.brand == brand)
        if category:
            q = q.filter(Document.category == category)
        docs = q.order_by(Document.upload_time.desc()).all()
        items = [
            {
                "id": d.id,
                "name": d.original_name,
                "brand": d.brand,
                "category": d.category,
            }
            for d in docs
        ]
        return {"brand": brand, "category": category, "items": items}
    finally:
        db.close()


def suggest_plc_model(requirements: str, top_k: int = 3):
    """Search hardware manuals to suggest a suitable PLC model based on project requirements.

    Args:
        requirements: Natural-language description of project needs
                      (e.g. 'need 64 DI/DO, Profinet, harsh environment -40°C~70°C').
        top_k: Number of result chunks to return (default 3).
    """
    return _search(requirements, top_k, category="hardware-manual")


def compare_plc_specs(brand_a: str, brand_b: str, keyword: str = "", top_k: int = 5):
    """Compare PLC hardware specifications between two brands.

    Searches the hardware-manual category for each brand and returns results
    side-by-side so Claude can synthesise a comparison table.

    Args:
        brand_a: First brand slug (e.g. 'siemens').
        brand_b: Second brand slug (e.g. 'mitsubishi').
        keyword: Optional focus keyword (e.g. 'CPU specifications', 'analog input').
                 Defaults to a broad overview query when omitted.
        top_k: Number of chunks per brand (default 5).
    """
    query = keyword.strip() if keyword.strip() else "CPU specifications overview analog digital IO"
    results_a = _search(query, top_k, brand_a, "hardware-manual")
    results_b = _search(query, top_k, brand_b, "hardware-manual")
    return {brand_a: results_a, brand_b: results_b}


def list_available_software(brand: str | None = None, category: str | None = None):
    """List available software from the database, filtered by brand/category."""
    from app.models.software import Software

    db = SessionLocal()
    try:
        q = db.query(Software).filter(Software.is_active == True)  # noqa: E712
        if brand:
            q = q.filter(Software.brand == brand)
        if category:
            q = q.filter(Software.category == category)
        sw_list = q.order_by(Software.created_at.desc()).all()
        items = [
            {
                "id": s.id,
                "name": s.original_name,
                "brand": s.brand,
                "category": s.category,
                "latest_version": s.latest_version,
            }
            for s in sw_list
        ]
        return {"brand": brand, "category": category, "items": items}
    finally:
        db.close()
