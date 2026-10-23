"""Composition root for the analytics context (visit tracking, dashboard statistics)."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

import app.modules.analytics.infrastructure.geo as geo_module
from app.modules.analytics.application.track_visit import TrackVisit
from app.modules.analytics.infrastructure.repositories import SqlVisitEventSink


@dataclass
class AnalyticsContext:
    track_visit: TrackVisit


def analytics(db: Session) -> AnalyticsContext:
    # resolve_ip_geo is read at call time so tests can monkeypatch the geo module.
    return AnalyticsContext(
        track_visit=TrackVisit(SqlVisitEventSink(db), geo_module.resolve_ip_geo),
    )
