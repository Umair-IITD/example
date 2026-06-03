"""
tests/test_sprint11_pii_masking.py

Aadhaar PII masking unit tests.

Covers:
- Plain 12-digit Aadhaar masked
- Space-separated format (4 4 4) masked
- Hyphen-separated format (4-4-4) masked
- Last 4 digits preserved; first 8 masked
- Non-Aadhaar digit strings not affected
- Multiple Aadhaar numbers in one text are all masked
- 11-digit number not masked (too short for \b to anchor a 12-digit group)
- 13-digit number not masked (trailing digit breaks word boundary)
- Empty string returns empty
- Text with no numbers unchanged
- Mixed content: only Aadhaar masked, other numbers preserved
"""
from __future__ import annotations

from app.pii_masking import mask_aadhaar


class TestMaskAadhaar:
    def test_plain_12_digit_masked(self):
        result = mask_aadhaar("Aadhaar: 123456789012")
        assert "XXXX XXXX 9012" in result
        assert "1234" not in result

    def test_spaced_format_masked(self):
        result = mask_aadhaar("Aadhaar: 1234 5678 9012")
        assert "XXXX XXXX 9012" in result

    def test_hyphenated_format_masked(self):
        result = mask_aadhaar("Aadhaar: 1234-5678-9012")
        assert "XXXX XXXX 9012" in result

    def test_last_4_preserved(self):
        result = mask_aadhaar("ID: 9876 5432 1011")
        assert "1011" in result

    def test_first_8_masked(self):
        result = mask_aadhaar("ID: 9876 5432 1011")
        assert "9876" not in result
        assert "5432" not in result

    def test_non_aadhaar_number_unchanged(self):
        result = mask_aadhaar("Order #12345 is confirmed")
        assert "12345" in result

    def test_short_number_unchanged(self):
        result = mask_aadhaar("Pin: 123456")
        assert "123456" in result

    def test_multiple_aadhaar_all_masked(self):
        text   = "First: 1111 2222 3333 Second: 4444 5555 6666"
        result = mask_aadhaar(text)
        assert "XXXX XXXX 3333" in result
        assert "XXXX XXXX 6666" in result
        assert "1111" not in result
        assert "4444" not in result

    def test_13_digit_not_masked(self):
        result = mask_aadhaar("Number: 1234567890123")
        assert "1234567890123" in result

    def test_11_digit_not_masked(self):
        result = mask_aadhaar("Number: 12345678901")
        assert "12345678901" in result

    def test_empty_string(self):
        assert mask_aadhaar("") == ""

    def test_no_numbers_unchanged(self):
        text = "OTP not received on my device."
        assert mask_aadhaar(text) == text

    def test_mixed_content_ticket_number_preserved(self):
        text   = "Ticket #5001: Aadhaar 1234 5678 9012 — OTP not received."
        result = mask_aadhaar(text)
        assert "5001" in result
        assert "XXXX XXXX 9012" in result

    def test_at_start_of_string(self):
        result = mask_aadhaar("123456789012 is my Aadhaar")
        assert "XXXX XXXX 9012" in result

    def test_at_end_of_string(self):
        result = mask_aadhaar("My Aadhaar is 123456789012")
        assert "XXXX XXXX 9012" in result
