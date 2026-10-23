"""JWT contract for the identity and upload-token codecs.

The tokens are plain HS256 JWS, so an independent stdlib HMAC implementation is the oracle: tokens
issued before the python-jose -> PyJWT swap must keep verifying, and tokens issued now must verify
without any JWT library.
"""
import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.utils import utcnow
from app.domain.auth import AuthError, BizCode
from app.domain.catalog import CatalogCode, CatalogError
from app.modules.catalog.infrastructure.tokens import JwtUploadTokenCodec
from app.modules.identity.infrastructure.tokens import JwtTokenCodec, decode_token

ROOT = Path(__file__).resolve().parents[2]
HASHES = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}


@pytest.fixture(autouse=True)
def hs256_secret(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "contract-test-secret-key-32-bytes!")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


def _claims(**overrides) -> dict:
    now = _now()
    return {"sub": "42", "type": "access", "jti": "abc123", "iat": now, "exp": now + 900, **overrides}


def _sign(claims: dict, secret: str | None = None, alg: str = "HS256") -> str:
    """HS256/HS512 JWS built with the stdlib only."""
    key = settings.JWT_SECRET_KEY if secret is None else secret
    head = _b64(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(key.encode(), f"{head}.{body}".encode(), HASHES[alg]).digest()
    return f"{head}.{body}.{_b64(mac)}"


def _bad_token(case: str) -> str:
    valid = _sign(_claims())
    head, body, sig = valid.split(".")
    makers = {
        "garbage": lambda: "not-a-token",
        "empty": lambda: "",
        "tampered signature": lambda: f"{head}.{body}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}",
        "expired": lambda: _sign(_claims(exp=_now() - 60)),
        "unsigned alg=none": lambda: f"{_b64(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{body}.",
        "HS512 outside the allow-list": lambda: _sign(_claims(), alg="HS512"),
        "signed with another secret": lambda: _sign(_claims(), secret="another-secret-key-of-32-bytes!!!!"),
        "sub is an int": lambda: _sign(_claims(sub=42)),
    }
    return makers[case]()


BAD_CASES = [
    "garbage",
    "empty",
    "tampered signature",
    "expired",
    "unsigned alg=none",
    "HS512 outside the allow-list",
    "signed with another secret",
    "sub is an int",
]


def test_token_from_an_independent_hs256_implementation_is_accepted():
    """What python-jose issued before the swap is plain HS256, so it must keep working."""
    claims = _claims()
    assert JwtTokenCodec().decode(_sign(claims)) == claims


def test_issued_token_verifies_with_plain_hmac():
    claims = _claims()
    head, body, sig = JwtTokenCodec().encode(claims).split(".")
    assert json.loads(_unb64(head)) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(_unb64(body)) == claims
    expected = _b64(hmac.new(settings.JWT_SECRET_KEY.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    assert hmac.compare_digest(sig, expected)


def test_naive_utc_datetimes_are_encoded_as_utc_epoch_seconds():
    """The session use case hands over naive-UTC datetimes for iat/exp."""
    issued = utcnow()
    token = JwtTokenCodec().encode({"sub": "1", "iat": issued, "exp": issued + timedelta(minutes=15)})
    payload = decode_token(token)
    assert payload["iat"] == int(issued.replace(tzinfo=UTC).timestamp())
    assert payload["exp"] == payload["iat"] + 900


def test_future_iat_is_tolerated():
    """python-jose never compared iat with the clock; PyJWT does unless told not to, and replicas' clocks differ."""
    future = _now() + 300
    assert JwtTokenCodec().decode(_sign(_claims(iat=future, exp=future + 900)))["iat"] == future


@pytest.mark.parametrize("case", BAD_CASES)
def test_identity_codec_rejects(case):
    with pytest.raises(AuthError) as exc_info:
        JwtTokenCodec().decode(_bad_token(case))
    assert exc_info.value.code == BizCode.AUTH_TOKEN_INVALID


def test_upload_token_round_trip():
    codec = JwtUploadTokenCodec()
    payload = codec.decode(codec.encode({"brand": "siemens", "version": "V18"}))
    assert payload["brand"] == "siemens"
    assert payload["version"] == "V18"
    assert payload["exp"] > _now()


@pytest.mark.parametrize("case", ["garbage", "expired", "signed with another secret"])
def test_upload_codec_rejects_with_a_reason(case):
    with pytest.raises(CatalogError) as exc_info:
        JwtUploadTokenCodec().decode(_bad_token(case))
    assert exc_info.value.code == CatalogCode.UPLOAD_TOKEN_INVALID
    assert exc_info.value.params["reason"]


JOSE_IMPORT = re.compile(r"^\s*(?:from|import)\s+jose\b", re.MULTILINE)


def test_app_does_not_import_python_jose():
    """python-jose has an unpatched algorithm-confusion advisory (GHSA-3qf3-8w2g-rqmx); PyJWT replaces it."""
    assert JOSE_IMPORT.search("from jose import jwt"), "the pattern must match what it guards against"
    app_dir = ROOT / "app"
    sources = list(app_dir.rglob("*.py"))
    assert (app_dir / "modules" / "identity" / "infrastructure" / "tokens.py") in sources, "scan must cover the codec"
    assert [str(p) for p in sources if JOSE_IMPORT.search(p.read_text(encoding="utf-8"))] == []


@pytest.mark.parametrize("manifest", ["requirements.txt", "requirements-app.txt", "requirements-heavy.txt"])
def test_manifests_do_not_declare_python_jose(manifest):
    assert "python-jose" not in (ROOT / manifest).read_text(encoding="utf-8").lower()
