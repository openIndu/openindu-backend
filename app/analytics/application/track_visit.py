"""Record one page-view visit event with IP geolocation."""
from collections.abc import Callable
from typing import Any, Protocol

from app.analytics.domain.visit_rules import PAGE_VIEW, clamp_path, clamp_user_agent, safe_visitor_id


class VisitEventSink(Protocol):
    def record(self, *, ip_address: str, client_id: str | None, event_type: str, user_agent: str, path: str, geo_location: str, country_code: str, is_authenticated: bool, user_id: int | None) -> None: ...


class TrackVisit:
    def __init__(self, sink: VisitEventSink, resolve_geo: Callable[[str], dict[str, Any]]) -> None:
        self.sink = sink
        self.resolve_geo = resolve_geo

    def execute(self, *, path: str, client_id: str | None, user_id: int | None, ip: str, user_agent: str) -> None:
        geo = self.resolve_geo(ip)
        self.sink.record(
            ip_address=ip,
            client_id=safe_visitor_id(client_id),
            event_type=PAGE_VIEW,
            user_agent=clamp_user_agent(user_agent),
            path=clamp_path(path),
            geo_location=str(geo["name"]),
            country_code=str(geo["country_code"]),
            is_authenticated=user_id is not None,
            user_id=user_id,
        )
