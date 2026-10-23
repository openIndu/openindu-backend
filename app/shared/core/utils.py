"""Shared utility helpers."""
import ipaddress as _ipaddress
import re
from datetime import UTC, datetime
from typing import Any as _Any  # avoid a forward-import of FastAPI's Request

from app.shared.core.err_code import ErrorCode
from app.shared.domain.auth import BizCode

# Allowed in OSS object names: letters/digits/CJK/dot/dash/underscore/space.
# Everything else (path separators, control chars, &, ?, #, …) is stripped so
# the resulting key is safe to embed in URLs without re-encoding traps.
_SAFE_NAME_RE = re.compile(r"[^\w.\- 一-鿿]")


def oss_key_for_upload(original_name: str, prefix: str) -> str:
    """Build a human-readable, stable OSS object key.

    Format: ``{prefix}/{sanitized_stem}.{ext}`` — no timestamp suffix.
    Re-uploading the same file name silently overwrites the previous object
    (OSS PUT is idempotent), which is the intended behaviour for admin-curated
    software packages.
    """
    if "." in original_name:
        stem, ext = original_name.rsplit(".", 1)
        ext = ext.lower()
    else:
        stem, ext = original_name, "bin"
    safe_stem = _SAFE_NAME_RE.sub("", stem).strip().strip(".") or "file"
    filename = f"{safe_stem}.{ext}"
    return f"{prefix.strip('/')}/{filename}"


def mask_phone(phone: str | None) -> str | None:
    """Mask a phone number for privacy-preserving display.

    A standard Chinese mobile number (11 digits) becomes ``138****0000`` —
    the first 3 and last 4 digits are kept, the middle is masked. Empty values
    (``None`` / ``""``) are returned unchanged so callers can fall back to a
    non-phone placeholder (e.g. ``ID:5``) without it being masked.
    """
    if not phone:
        return phone
    s = phone.strip()
    if len(s) >= 7:
        return f"{s[:3]}****{s[-4:]}"
    return s


# Any 11-digit mainland mobile number (1 + 10 digits) embedded in free text.
_PHONE_IN_TEXT_RE = re.compile(r"(?<!\d)(1\d{2})(\d{4})(\d{4})(?!\d)")


def mask_phones_in_text(text: str | None) -> str | None:
    """Mask every 11-digit phone number embedded in an arbitrary string.

    Used for audit-log ``detail`` blobs, which may carry a phone in JSON-ish
    text (e.g. ``{'phone': '13000000000'}``). Each match becomes ``138****0000``.
    The negative look-arounds keep us from chopping into longer digit runs.
    """
    if not text:
        return text
    return _PHONE_IN_TEXT_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", text)


def ok(data=None, message: str | None = None, *, biz_code: BizCode = BizCode.OK):
    """Standard success envelope shared by all API routers.

    Use an explicit ``None`` check, not ``data or {}`` — an empty list (e.g. a
    brand+category combo with no series) is falsy and would be coerced to ``{}``,
    breaking array consumers on the frontend (``.filter is not a function``).
    """
    return {
        "code": 200,
        "biz_code": biz_code.value,
        "message": message if message is not None else ErrorCode.message(biz_code),
        "data": data if data is not None else {},
    }


# ---------------------------------------------------------------------------
# Datetime serialization
#
# Every DateTime column in our SQLAlchemy models is stored as a NAIVE UTC
# datetime (we strip tzinfo before INSERT, see `utcnow()` above).
# When such a value is serialized via ``dt.isoformat()`` directly, the output
# has no timezone marker — e.g. ``2026-06-25T09:37:53.369247``. Browsers'
# ``new Date(...)`` then parses that as **local time**, which on a CST machine
# shifts every timestamp by ±8h.
#
# ``iso_utc(dt)`` is the single chokepoint that adds the ``+00:00`` marker so
# the wire format is unambiguous. The frontend then renders with
# ``new Date(s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })``.
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """Naive UTC now — the storage convention across all models."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(dt: datetime | None) -> str | None:
    """Serialize a (naive or aware) UTC datetime as ISO 8601 with explicit
    ``+00:00`` marker. Returns ``None`` for ``None`` so it slots into dict
    builders that need a JSON-null on the wire."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Real client IP resolution
#
# Behind one or more reverse proxies (K8s Ingress, nginx, an SLB), the TCP
# peer is the last proxy, not the user. Real IP travels in HTTP headers —
# *if* every hop is configured to set/append it. Header preference, in order:
#
#   1. ``X-Forwarded-For``  — historical standard. May be a comma-separated
#      chain like ``client, proxy1, proxy2``; the **leftmost public address**
#      is the client. We walk left→right and skip private/loopback hops so
#      a misbehaving inner proxy doesn't pollute the answer. Falls back to
#      the leftmost token if every entry is private (single-hop docker dev).
#   2. ``X-Real-IP``        — what nginx-ingress sets by default; many setups
#      have this and not XFF. Honoured as a fallback so we don't lose the
#      real IP just because the front door uses one convention over the other.
#   3. ``request.client.host`` — TCP peer. With Uvicorn's --proxy-headers,
#      this already reflects the parsed XFF when one is present, so this
#      branch is the last-resort path (containerised dev without a proxy).
#
# Returns ``None`` only if the request has no client at all (extremely rare
# in tests). Callers wanting a string sentinel should ``... or "unknown"``.
# ---------------------------------------------------------------------------

def _is_private(addr: str) -> bool:
    """True for loopback / RFC1918 / link-local / unique-local — i.e. not a
    real client address. Returns True on parse failures so a bogus entry is
    also skipped during XFF walking."""
    try:
        ip = _ipaddress.ip_address(addr)
    except ValueError:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def real_client_ip(request: _Any) -> str | None:
    """Best-effort real-IP extraction from request headers / TCP peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidates = [p.strip() for p in xff.split(",") if p.strip()]
        for cand in candidates:
            if not _is_private(cand):
                return cand
        if candidates:
            return candidates[0]
    real = request.headers.get("x-real-ip")
    if real:
        real = real.strip()
        if real:
            return real
    return request.client.host if request.client else None
