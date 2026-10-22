"""SQLAlchemy adapters for the analytics context."""
from sqlalchemy.orm import Session

from app.models.visit_event import VisitEvent


class SqlVisitEventSink:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(self, *, ip_address: str, client_id: str | None, event_type: str, user_agent: str, path: str, geo_location: str, country_code: str, is_authenticated: bool, user_id: int | None) -> None:
        event = VisitEvent(
            ip_address=ip_address,
            client_id=client_id,
            event_type=event_type,
            user_agent=user_agent,
            path=path,
            geo_location=geo_location,
            country_code=country_code,
            is_authenticated=is_authenticated,
            user_id=user_id,
        )
        self.db.add(event)
        self.db.commit()
