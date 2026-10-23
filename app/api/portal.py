"""Portal content API — serves hero, solutions, carousel etc. to the public frontend."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.shared.core.dependencies import get_db, require_admin
from app.shared.core.utils import ok
from app.shared.models.portal_content import PortalContent
from app.shared.models.user import User

router = APIRouter(prefix="/portal")


class ContentIn(BaseModel):
    content: dict = {}
    sort_order: int = 0


@router.get("/hero")
async def hero(db: Session = Depends(get_db)):
    item = (
        db.query(PortalContent)
        .filter(PortalContent.section == "hero", PortalContent.is_active.is_(True))
        .order_by(PortalContent.sort_order)
        .first()
    )
    return ok(item.to_dict() if item else {})


@router.get("/solutions")
async def solutions(db: Session = Depends(get_db)):
    items = (
        db.query(PortalContent)
        .filter(PortalContent.section == "solutions", PortalContent.is_active.is_(True))
        .order_by(PortalContent.sort_order)
        .all()
    )
    return ok({"items": [i.to_dict() for i in items]})


@router.post("/solutions")
async def create_solution(body: ContentIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    item = PortalContent(section="solutions", content=body.content, sort_order=body.sort_order)
    db.add(item)
    db.commit()
    db.refresh(item)
    return ok(item.to_dict())


@router.put("/solutions/{item_id}")
async def update_solution(item_id: int, body: ContentIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    item = db.query(PortalContent).filter(PortalContent.id == item_id).first()
    if not item:
        raise HTTPException(404, "内容不存在")
    item.content = body.content
    item.sort_order = body.sort_order
    db.commit()
    db.refresh(item)
    return ok(item.to_dict())


@router.delete("/solutions/{item_id}")
async def delete_solution(item_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    item = db.query(PortalContent).filter(PortalContent.id == item_id).first()
    if not item:
        raise HTTPException(404, "内容不存在")
    db.delete(item)
    db.commit()
    return ok(message="删除成功")
