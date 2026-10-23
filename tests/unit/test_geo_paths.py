"""The geo module finds the backend root by counting directories up from its own file.

Nothing else exercises that count: if a package move left it one level off, every lookup would
quietly fall back to "unknown" instead of failing.
"""
from app.modules.analytics.infrastructure import geo


def test_backend_root_is_the_repository_root():
    assert (geo._BACKEND_ROOT / "app" / "web_app.py").is_file()


def test_default_database_path_points_at_the_bundled_xdb(monkeypatch):
    monkeypatch.setattr(geo.settings, "IP2REGION_XDB_PATH", "")
    assert geo._xdb_path() == geo._BACKEND_ROOT / "data" / "ip2region_v4.xdb"
    assert geo._xdb_path().is_file()


def test_relative_configured_path_is_resolved_against_the_backend_root(monkeypatch):
    monkeypatch.setattr(geo.settings, "IP2REGION_XDB_PATH", "data/ip2region_v4.xdb")
    assert geo._xdb_path() == geo._BACKEND_ROOT / "data" / "ip2region_v4.xdb"
    assert geo._xdb_path().is_file()
