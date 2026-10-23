"""Brand mapping API."""
from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.shared.core.dependencies import get_db, require_auth
from app.shared.core.utils import ok
from app.shared.models.brand_mapping import BrandMapping
from app.shared.models.user import User

router = APIRouter(prefix="/brand-mapping")

BRAND_OVERVIEW = {
    "siemens": "西门子 S7-1200 / S7-1500",
    "mitsubishi": "三菱 FX5U / R 系列",
    "omron": "欧姆龙 NJ / NX 系列",
    "keyence": "基恩士 KV-8000",
    "inovance": "汇川 AM600 / Easy 系列",
}


@router.get("/overview")
async def overview(user: User = Depends(require_auth)):
    return ok(BRAND_OVERVIEW)


@router.get("/address")
async def address(
    source_brand: str | None = None,
    target_brand: str | None = None,
    item: str | None = None,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    q = db.query(BrandMapping)
    if source_brand:
        q = q.filter(BrandMapping.source_brand == source_brand.lower())
    if target_brand:
        q = q.filter(BrandMapping.target_brand == target_brand.lower())
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
    return ok({"source_brand": source_brand, "target_brand": target_brand, "item": item, "mappings": mappings})
