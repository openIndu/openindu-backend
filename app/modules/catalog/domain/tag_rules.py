"""Resource tag rules without framework dependencies."""
from app.domain.catalog import CatalogCode, CatalogError

VALID_TAG_TYPES = {"doc_brand", "sw_brand", "doc_category", "sw_category", "doc_series"}
SERIES_TYPES = {"doc_series"}


def require_known_tag_type(tag_type: str) -> None:
    if tag_type not in VALID_TAG_TYPES:
        raise CatalogError(CatalogCode.TAG_TYPE_INVALID, options=", ".join(sorted(VALID_TAG_TYPES)))


def require_tag_fields(value: str, label_zh: str) -> tuple[str, str]:
    stripped_value, stripped_label = (value or "").strip(), (label_zh or "").strip()
    if not stripped_value or not stripped_label:
        raise CatalogError(CatalogCode.TAG_VALUE_AND_LABEL_REQUIRED)
    return stripped_value, stripped_label


def require_series_parent(tag_type: str, parent_value: str | None, brand_value: str | None) -> None:
    if tag_type in SERIES_TYPES and (not parent_value or not brand_value):
        raise CatalogError(CatalogCode.TAG_SERIES_PARENT_REQUIRED)


def require_new_value(value: str | None) -> str:
    stripped = (value or "").strip()
    if not stripped:
        raise CatalogError(CatalogCode.TAG_VALUE_REQUIRED)
    return stripped


def require_new_label(label_zh: str | None) -> str:
    stripped = (label_zh or "").strip()
    if not stripped:
        raise CatalogError(CatalogCode.TAG_LABEL_REQUIRED)
    return stripped
