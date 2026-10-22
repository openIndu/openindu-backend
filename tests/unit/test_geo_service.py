"""Unit tests for IP -> region resolution (analytics context geo module)."""
import pytest

from app.analytics.infrastructure import geo as geo_service
from app.analytics.infrastructure.geo import (
    _format_cn,
    _province_key,
    lookup_point,
    resolve_ip_geo,
)


def test_province_key_strips_suffixes():
    assert _province_key("四川省") == "四川"
    assert _province_key("北京市") == "北京"
    assert _province_key("新疆维吾尔自治区") == "新疆"
    assert _province_key("广西壮族自治区") == "广西"
    assert _province_key("内蒙古自治区") == "内蒙古"
    assert _province_key("香港特别行政区") == "香港"
    assert _province_key("北京") == "北京"  # already a stem


def test_format_cn_joins_and_dedupes():
    assert _format_cn("四川省", "成都市", "移动") == "四川省 成都市 移动"
    assert _format_cn("江苏省", "南京市", "0") == "江苏省 南京市"   # unknown isp dropped
    assert _format_cn("北京", "北京市", "联通") == "北京市 联通"     # municipality dedup
    assert _format_cn("0", "0", "0") == "中国"                      # all unknown


def test_lookup_point_province_country_unknown():
    cn = lookup_point("四川省 成都市 移动", "CN")
    assert cn["country_code"] == "CN"
    assert (round(cn["lat"], 2), round(cn["lng"], 2)) == (30.57, 104.07)

    us = lookup_point("美国", "US")
    assert us["country_code"] == "US" and us["lat"] == 37.0902

    local = lookup_point("本地开发", "LOCAL")
    assert local["country_code"] == "LOCAL"

    unk = lookup_point("未知", "UNKNOWN")
    assert unk["country_code"] == "UNKNOWN" and (unk["lat"], unk["lng"]) == (20.0, 0.0)


@pytest.mark.parametrize("ip", ["", None, "not-an-ip"])
def test_resolve_invalid_is_unknown(ip):
    r = resolve_ip_geo(ip)
    assert r["name"] == "未知" and r["country_code"] == "UNKNOWN"


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.1", "10.0.0.5"])
def test_resolve_private_is_local(ip):
    r = resolve_ip_geo(ip)
    assert r["name"] == "本地开发" and r["country_code"] == "LOCAL"


def test_resolve_public_cn_ip():
    """Requires the ip2region xdb + py-ip2region; skipped if unavailable."""
    if geo_service._get_searcher() is None:
        pytest.skip("ip2region xdb / py-ip2region not available")
    r = resolve_ip_geo("117.174.151.206")
    assert r["country_code"] == "CN"
    assert r["name"].startswith("四川")
    assert (round(r["lat"], 1), round(r["lng"], 1)) == (30.6, 104.1)


def test_resolve_public_foreign_ip():
    if geo_service._get_searcher() is None:
        pytest.skip("ip2region xdb / py-ip2region not available")
    r = resolve_ip_geo("8.8.8.8")
    assert r["country_code"] == "US"
    assert r["name"] == "美国"
