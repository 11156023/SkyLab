"""flag_service 純函式單元測試（不需 DB）。"""

import hashlib

from app.services.course import flag_service


class TestNormalizeAnswer:
    def test_strips_surrounding_whitespace(self):
        assert flag_service.normalize_answer("  FLAG{x}  ") == "FLAG{x}"

    def test_strips_newlines_and_tabs(self):
        assert flag_service.normalize_answer("\tFLAG{x}\n") == "FLAG{x}"

    def test_preserves_case_and_inner_spaces(self):
        assert flag_service.normalize_answer("Flag{A b C}") == "Flag{A b C}"

    def test_empty_becomes_empty(self):
        assert flag_service.normalize_answer("   ") == ""


class TestHashFlag:
    def test_sha256_hexdigest_of_normalized_value(self):
        expected = hashlib.sha256(b"FLAG{hello}").hexdigest()
        assert flag_service.hash_flag(" FLAG{hello} ") == expected

    def test_hash_length_is_64(self):
        assert len(flag_service.hash_flag("x")) == 64


class TestVerifyFlag:
    def test_correct_answer_matches(self):
        stored = flag_service.hash_flag("FLAG{secret}")
        assert flag_service.verify_flag("FLAG{secret}", stored) is True

    def test_answer_with_whitespace_matches(self):
        stored = flag_service.hash_flag("FLAG{secret}")
        assert flag_service.verify_flag("  FLAG{secret}\n", stored) is True

    def test_wrong_answer_fails(self):
        stored = flag_service.hash_flag("FLAG{secret}")
        assert flag_service.verify_flag("FLAG{nope}", stored) is False

    def test_case_sensitive(self):
        stored = flag_service.hash_flag("FLAG{Secret}")
        assert flag_service.verify_flag("FLAG{secret}", stored) is False

    def test_none_answer_fails(self):
        stored = flag_service.hash_flag("FLAG{secret}")
        assert flag_service.verify_flag(None, stored) is False

    def test_empty_answer_fails(self):
        stored = flag_service.hash_flag("FLAG{secret}")
        assert flag_service.verify_flag("", stored) is False

    def test_missing_hash_fails(self):
        assert flag_service.verify_flag("FLAG{secret}", None) is False


class TestProgressPercent:
    def test_zero_total_is_zero(self):
        assert flag_service.progress_percent(0, 0) == 0.0

    def test_half(self):
        assert flag_service.progress_percent(5, 10) == 50.0

    def test_full(self):
        assert flag_service.progress_percent(10, 10) == 100.0

    def test_rounds_to_one_decimal(self):
        assert flag_service.progress_percent(1, 3) == 33.3

    def test_completed_capped_at_total(self):
        assert flag_service.progress_percent(11, 10) == 100.0
