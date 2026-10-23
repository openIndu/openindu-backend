"""Unit tests for phone masking helper."""
from app.shared.core.utils import mask_phone


class TestMaskPhone:
    def test_standard_chinese_mobile(self):
        assert mask_phone("13800000000") == "138****0000"
        assert mask_phone("13912345678") == "139****5678"

    def test_keeps_first_three_last_four(self):
        masked = mask_phone("18601234567")
        assert masked.startswith("186")
        assert masked.endswith("4567")
        assert "****" in masked

    def test_none_returned_unchanged(self):
        assert mask_phone(None) is None

    def test_empty_returned_unchanged(self):
        assert mask_phone("") == ""

    def test_short_value_not_masked(self):
        # Too short to mask meaningfully — returned as-is.
        assert mask_phone("123") == "123"

    def test_whitespace_trimmed(self):
        assert mask_phone("  13800000000  ") == "138****0000"
