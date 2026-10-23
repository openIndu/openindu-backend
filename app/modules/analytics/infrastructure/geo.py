"""IP geo resolution for dashboard statistics, backed by ip2region (offline xdb).

Resolution contract
-------------------
``resolve_ip_geo(ip)`` returns a coarse geo point for an IP address:
  * private / loopback       -> "本地开发"
  * public IP via ip2region  -> "省 市 运营商" (China) or the country name (abroad)
  * unresolved / no database -> "未知"  (graceful fallback)

The ip2region v4 xdb (``data/ip2region_v4.xdb``) is loaded fully into memory once,
on first use, and shared read-only across threads (the buffer-backed searcher does
pure in-memory binary search, so concurrent ``search`` is safe). If the xdb file or
the ``py-ip2region`` package is unavailable, resolution degrades to "未知" for public
IPs instead of raising — the app still boots without the database present.

xdb "region" string format (v4):  ``国家|省份|城市|运营商|国家码``  e.g. ``中国|四川省|成都市|移动|CN``
"""
from __future__ import annotations  # noqa: I001

import ipaddress
import logging
import threading
from pathlib import Path

from app.shared.core.config import settings

logger = logging.getLogger(__name__)

# Backend repo root: app/modules/analytics/infrastructure/geo.py -> parents[4]
_BACKEND_ROOT = Path(__file__).resolve().parents[4]

# Map coordinates. China is plotted per-province (keys are normalized province
# stems, see _province_key); everything else is plotted per country code.
PROVINCE_POINTS: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074), "天津": (39.3434, 117.3616), "河北": (38.0428, 114.5149),
    "山西": (37.8706, 112.5489), "内蒙古": (40.8414, 111.7519), "辽宁": (41.8057, 123.4315),
    "吉林": (43.8868, 125.3245), "黑龙江": (45.8038, 126.5340), "上海": (31.2304, 121.4737),
    "江苏": (32.0603, 118.7969), "浙江": (30.2741, 120.1551), "安徽": (31.8206, 117.2290),
    "福建": (26.0745, 119.2965), "江西": (28.6820, 115.8579), "山东": (36.6512, 117.1201),
    "河南": (34.7466, 113.6254), "湖北": (30.5928, 114.3055), "湖南": (28.2282, 112.9388),
    "广东": (23.1291, 113.2644), "广西": (22.8170, 108.3665), "海南": (20.0440, 110.1999),
    "重庆": (29.5630, 106.5516), "四川": (30.5728, 104.0668), "贵州": (26.6470, 106.6302),
    "云南": (24.8801, 102.8329), "西藏": (29.6500, 91.1000), "陕西": (34.3416, 108.9398),
    "甘肃": (36.0611, 103.8343), "青海": (36.6171, 101.7782), "宁夏": (38.4872, 106.2309),
    "新疆": (43.8256, 87.6168), "台湾": (25.0330, 121.5654), "香港": (22.3193, 114.1694),
    "澳门": (22.1987, 113.5439),
}
COUNTRY_POINTS: dict[str, tuple[float, float]] = {
    "CN": (35.8617, 104.1954), "US": (37.0902, -95.7129), "JP": (36.2048, 138.2529),
    "DE": (51.1657, 10.4515), "SG": (1.3521, 103.8198), "GB": (55.3781, -3.4360),
    "KR": (35.9078, 127.7669), "FR": (46.2276, 2.2137), "CA": (56.1304, -106.3468),
    "AU": (-25.2744, 133.7751), "RU": (61.5240, 105.3188), "IN": (20.5937, 78.9629),
}
# Country code -> Chinese name (the xdb gives English names abroad; we display 中文).
COUNTRY_NAMES_CN: dict[str, str] = {
    "US": "美国", "JP": "日本", "KR": "韩国", "KP": "朝鲜", "GB": "英国", "DE": "德国",
    "FR": "法国", "IT": "意大利", "ES": "西班牙", "PT": "葡萄牙", "NL": "荷兰", "BE": "比利时",
    "CH": "瑞士", "AT": "奥地利", "IE": "爱尔兰", "SE": "瑞典", "NO": "挪威", "DK": "丹麦",
    "FI": "芬兰", "PL": "波兰", "CZ": "捷克", "HU": "匈牙利", "RO": "罗马尼亚", "GR": "希腊",
    "UA": "乌克兰", "RU": "俄罗斯", "TR": "土耳其", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西",
    "AR": "阿根廷", "CL": "智利", "AU": "澳大利亚", "NZ": "新西兰", "SG": "新加坡", "MY": "马来西亚",
    "TH": "泰国", "VN": "越南", "ID": "印度尼西亚", "PH": "菲律宾", "IN": "印度", "PK": "巴基斯坦",
    "BD": "孟加拉国", "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝", "MN": "蒙古", "KZ": "哈萨克斯坦",
    "SA": "沙特阿拉伯", "AE": "阿联酋", "IL": "以色列", "EG": "埃及", "ZA": "南非", "NG": "尼日利亚",
    "HK": "中国香港", "MO": "中国澳门", "TW": "中国台湾",
}
_LOCAL_POINT = (31.2304, 121.4737)
_UNKNOWN_POINT = (20.0, 0.0)

# Province-name suffixes stripped to get a stable lookup key. Longest first so
# "新疆维吾尔自治区" loses the full suffix, not just "自治区".
_PROVINCE_SUFFIXES = ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市")


def _province_key(token: str) -> str:
    token = (token or "").strip()
    for suffix in _PROVINCE_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def lookup_point(name: str | None, country_code: str | None = None) -> dict[str, float | str]:
    """Resolve a stored geo_location name (+optional country_code) to map coords.

    Used by the dashboard geo distribution: the first whitespace token of ``name``
    is treated as the province (China) and matched against PROVINCE_POINTS; foreign
    rows fall back to the country code, then to the "未知" point.
    """
    key = _province_key((name or "").split(" ")[0])
    if key in PROVINCE_POINTS:
        lat, lng = PROVINCE_POINTS[key]
        return {"country_code": country_code or "CN", "lat": lat, "lng": lng}
    if name == "本地开发" or country_code == "LOCAL":
        return {"country_code": "LOCAL", "lat": _LOCAL_POINT[0], "lng": _LOCAL_POINT[1]}
    if country_code and country_code in COUNTRY_POINTS:
        lat, lng = COUNTRY_POINTS[country_code]
        return {"country_code": country_code, "lat": lat, "lng": lng}
    return {"country_code": country_code or "UNKNOWN", "lat": _UNKNOWN_POINT[0], "lng": _UNKNOWN_POINT[1]}


# --- ip2region searcher (lazy, in-memory, thread-safe) -----------------------
_searcher = None
_searcher_loaded = False
_searcher_lock = threading.Lock()


def _xdb_path() -> Path:
    configured = (settings.IP2REGION_XDB_PATH or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else _BACKEND_ROOT / path
    return _BACKEND_ROOT / "data" / "ip2region_v4.xdb"


def _get_searcher():
    """Return a process-wide buffer-backed searcher, or None if unavailable."""
    global _searcher, _searcher_loaded
    if _searcher_loaded:
        return _searcher
    with _searcher_lock:
        if _searcher_loaded:
            return _searcher
        path = _xdb_path()
        try:
            import ip2region.searcher as xdb
            import ip2region.util as util

            util.verify_from_file(str(path))
            buffer = util.load_content_from_file(str(path))
            _searcher = xdb.new_with_buffer(util.IPv4, buffer)
            logger.info("ip2region xdb loaded from %s", path)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never block startup
            _searcher = None
            logger.warning("ip2region xdb unavailable (%s); geo resolution falls back to 未知", exc)
        _searcher_loaded = True
        return _searcher


def _format_cn(province: str, city: str, isp: str) -> str:
    """Join the meaningful China parts; "0" means the field is unknown."""
    parts: list[str] = []
    if province and province != "0":
        parts.append(province)
    if city and city != "0":
        if parts and city.startswith(parts[-1]):
            parts[-1] = city  # 北京 -> 北京市 : keep the more specific, avoid duplication
        elif city not in parts:
            parts.append(city)
    if isp and isp != "0":
        parts.append(isp)
    return " ".join(parts) or "中国"


def _resolve(ip: str | None) -> tuple[str, str]:
    """Return (display_name, country_code) for an IP."""
    if not ip:
        return "未知", "UNKNOWN"
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        parsed = None
    if parsed is not None and (parsed.is_private or parsed.is_loopback):
        return "本地开发", "LOCAL"

    searcher = _get_searcher()
    if searcher is None:
        return "未知", "UNKNOWN"
    try:
        region = searcher.search(ip) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("ip2region search failed for %s: %s", ip, exc)
        return "未知", "UNKNOWN"

    fields = region.split("|")
    fields += [""] * (5 - len(fields))
    country, province, city, isp, code = (f.strip() for f in fields[:5])
    if not region or country in ("", "0", "Reserved") or code in ("", "0"):
        return "未知", "UNKNOWN"
    if code == "CN":
        return _format_cn(province, city, isp), "CN"
    return (COUNTRY_NAMES_CN.get(code) or country or "未知"), (code or "UNKNOWN")


def resolve_ip_geo(ip: str | None) -> dict[str, str | float]:
    """Resolve an IP to a dashboard geo point: {name, country_code, lat, lng}."""
    name, code = _resolve(ip)
    point = lookup_point(name, code)
    return {
        "name": name,
        "country_code": code,
        "lat": float(point["lat"]),
        "lng": float(point["lng"]),
    }
