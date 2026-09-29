"""克隆 hostname：範本名稱（自由文字）當預設值時必須轉成合法 DNS label。"""

from __future__ import annotations

import re

import pytest

from app.exceptions import BadRequestError
from app.services.template.clone_service import _build_hostnames

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def test_ascii_template_name_becomes_valid_label() -> None:
    [hostname] = _build_hostnames(None, "Ubuntu 22.04 Lab", 1)
    assert _LABEL.match(hostname)
    assert hostname == "ubuntu-22-04-lab"


def test_unicode_template_name_batch_gives_distinct_valid_labels() -> None:
    hostnames = _build_hostnames(None, "Web 開發環境", 3)
    assert len(set(hostnames)) == 3
    for hostname in hostnames:
        assert len(hostname) <= 63
        assert _LABEL.match(hostname), hostname


@pytest.mark.parametrize("count", [1, 2])
def test_long_template_name_is_truncated_to_label_limit(count: int) -> None:
    hostnames = _build_hostnames(None, "x" * 200, count)
    assert len(hostnames) == count
    for hostname in hostnames:
        assert len(hostname) <= 63
        assert _LABEL.match(hostname)


def test_long_unicode_template_name_fits_after_punycode() -> None:
    for count in (1, 5):
        for hostname in _build_hostnames(None, "資訊工程學系" * 20, count):
            assert len(hostname) <= 63
            assert _LABEL.match(hostname), hostname


def test_template_name_without_usable_characters_falls_back_to_vm() -> None:
    assert _build_hostnames(None, "!!! ___ ???", 1) == ["vm"]
    assert _build_hostnames(None, "---", 2) == ["vm-01", "vm-02"]


def test_user_hostname_is_kept_and_batch_prefix_drops_trailing_separators() -> None:
    assert _build_hostnames("my-lab", "ignored", 1) == ["my-lab"]
    dotted = "a" * 58 + ".bc"  # 前 59 字元以 "." 結尾
    assert _build_hostnames(dotted, "ignored", 2) == ["a" * 58 + "-01", "a" * 58 + "-02"]


def test_unencodable_hostname_is_bad_request() -> None:
    with pytest.raises(BadRequestError):
        _build_hostnames("a..b", "ignored", 1)
