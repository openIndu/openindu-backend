"""Behavior tests for the analytics TrackVisit use case."""
from app.modules.analytics.application.track_visit import TrackVisit


class FakeSink:
    def __init__(self):
        self.events = []

    def record(self, **kwargs):
        self.events.append(kwargs)


def _use_case(geo=None):
    """Return a TrackVisit bound to its own FakeSink; read `uc.sink.events`."""
    return TrackVisit(FakeSink(), geo or (lambda ip: {"name": "四川省 成都市", "country_code": "CN"}))


def test_records_page_view_with_geo_and_identity():
    sink = FakeSink()
    TrackVisit(sink, lambda ip: {"name": "四川省 成都市", "country_code": "CN"}).execute(
        path="/resources", client_id="client-1", user_id=7, ip="8.8.8.8", user_agent="UA"
    )
    assert sink.events == [
        {
            "ip_address": "8.8.8.8",
            "client_id": "client-1",
            "event_type": "page_view",
            "user_agent": "UA",
            "path": "/resources",
            "geo_location": "四川省 成都市",
            "country_code": "CN",
            "is_authenticated": True,
            "user_id": 7,
        }
    ]


def test_anonymous_visit_is_not_authenticated():
    uc = _use_case()
    uc.execute(path="/", client_id=None, user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[0]["is_authenticated"] is False
    assert uc.sink.events[0]["user_id"] is None


def test_geo_values_are_coerced_to_str():
    uc = _use_case(lambda ip: {"name": 123, "country_code": 456})
    uc.execute(path="/", client_id=None, user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[0]["geo_location"] == "123"
    assert uc.sink.events[0]["country_code"] == "456"


def test_client_id_is_trimmed_and_capped():
    uc = _use_case()
    uc.execute(path="/", client_id="  spaced  ", user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[0]["client_id"] == "spaced"
    uc.execute(path="/", client_id="x" * 100, user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[1]["client_id"] == "x" * 64


def test_blank_client_id_becomes_none():
    uc = _use_case()
    uc.execute(path="/", client_id="   ", user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[0]["client_id"] is None


def test_long_user_agent_is_clamped():
    uc = _use_case()
    uc.execute(path="/", client_id=None, user_id=None, ip="1.2.3.4", user_agent="u" * 600)
    assert uc.sink.events[0]["user_agent"] == "u" * 512


def test_long_path_is_clamped_and_empty_path_defaults_to_root():
    uc = _use_case()
    uc.execute(path="/p" * 400, client_id=None, user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[0]["path"] == "/p" * 256
    uc.execute(path="", client_id=None, user_id=None, ip="1.2.3.4", user_agent="")
    assert uc.sink.events[1]["path"] == "/"
