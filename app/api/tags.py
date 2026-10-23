"""Resource tag API — thin adapter over the catalog context (tag board)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_admin
from app.core.utils import ok
from app.models.document import Document
from app.models.resource_tag import ResourceTag
from app.models.software import Software
from app.models.user import User
from app.modules.catalog.domain.tag_rules import VALID_TAG_TYPES
from app.modules.catalog.provider import catalog

router = APIRouter(prefix="/tags")

SERIES_TYPES = {"doc_series"}


class CreateTagBody(BaseModel):
    type: str
    value: str
    label_zh: str
    parent_value: str | None = None
    brand_value: str | None = None
    sort_order: int = 0


class UpdateTagBody(BaseModel):
    value: str | None = None
    label_zh: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.get("")
async def list_tags(type: str | None = None, parent: str | None = None, brand: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ResourceTag)
    if type:
        if type not in VALID_TAG_TYPES:
            raise HTTPException(400, f"无效 type，可选：{', '.join(sorted(VALID_TAG_TYPES))}")
        q = q.filter(ResourceTag.type == type)
    if parent is not None:
        q = q.filter(ResourceTag.parent_value == parent)
    if brand is not None:
        q = q.filter(ResourceTag.brand_value == brand)
    tags = q.order_by(ResourceTag.type, ResourceTag.sort_order, ResourceTag.id).all()

    # When a specific type is requested, bulk-compute usage counts so the UI
    # can show "未使用" badges + filter. Doing this in one GROUP BY keeps it
    # O(1) queries per request regardless of tag count. When the caller doesn't
    # specify a type (rare — just the legacy "give me everything" case), skip
    # the join to avoid running 5 separate counts and keep this fast.
    usage_map: dict[str, int] = {}
    if type == "doc_brand":
        rows = db.query(Document.brand, func.count(Document.id)).group_by(Document.brand).all()
        usage_map = {b: c for b, c in rows if b is not None}
    elif type == "sw_brand":
        rows = db.query(Software.brand, func.count(Software.id)).group_by(Software.brand).all()
        usage_map = {b: c for b, c in rows if b is not None}
    elif type == "doc_category":
        rows = db.query(Document.category, func.count(Document.id)).group_by(Document.category).all()
        usage_map = {c: n for c, n in rows if c is not None}
    elif type == "sw_category":
        rows = db.query(Software.category, func.count(Software.id)).group_by(Software.category).all()
        usage_map = {c: n for c, n in rows if c is not None}
    elif type == "doc_series":
        rows = db.query(Document.series, func.count(Document.id)).group_by(Document.series).all()
        usage_map = {s: n for s, n in rows if s is not None}

    def tag_dict(t: ResourceTag) -> dict:
        d = t.to_dict()
        if type:  # only annotate when we actually computed the counts
            d["usage_count"] = int(usage_map.get(t.value, 0))
        return d

    return ok([tag_dict(t) for t in tags])


@router.post("")
async def create_tag(body: CreateTagBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    tag = catalog(db).create_tag.execute(
        tag_type=body.type, value=body.value, label_zh=body.label_zh,
        parent_value=body.parent_value, brand_value=body.brand_value, sort_order=body.sort_order,
    )
    return ok(tag.to_dict(), "创建成功")


@router.patch("/{tag_id}")
async def update_tag(tag_id: int, body: UpdateTagBody, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    tag = catalog(db).update_tag.execute(
        tag_id,
        value=body.value, label_zh=body.label_zh,
        is_active=body.is_active, sort_order=body.sort_order,
    )
    return ok(tag.to_dict(), "更新成功")


@router.delete("/{tag_id}")
async def delete_tag(tag_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    catalog(db).delete_tag.execute(tag_id)
    return ok(message="删除成功")
