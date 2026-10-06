"""Linux VM 登入帳號（cloud-init ciuser）命名政策的純函式測試。"""

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.domain import username_policy
from app.domain.username_policy import (
    LinuxUsername,
    errors_of,
    load_policy,
    validate_username,
)


def _codes(name: str) -> list[str]:
    return [v.code for v in validate_username(name)]


@pytest.mark.parametrize(
    ("name", "expected_errors", "expected_warnings"),
    [
        ("jerry", [], []),
        ("student01", [], []),
        ("lab_user-2", [], []),
        ("a" * 32, [], []),
        ("admin", ["LNX_RESERVED"], []),
        ("root", ["LNX_RESERVED"], []),
        ("docker", ["LNX_RESERVED"], []),
        ("Admin", ["LNX_FORMAT"], []),
        ("1user", ["LNX_FORMAT"], []),
        ("user.name", ["LNX_FORMAT"], []),
        ("user$", ["LNX_FORMAT"], []),
        ("a" * 33, ["LNX_FORMAT"], []),
        ("", ["LNX_FORMAT"], []),
        (" jerry", ["LNX_FORMAT"], []),
        ("jerry ", ["LNX_FORMAT"], []),
        ("ｊｅｒｒｙ", ["LNX_FORMAT"], []),
        ("學生", ["LNX_FORMAT"], []),
        ("systemd-foo", ["LNX_RESERVED_PREFIX"], []),
        ("libvirtd", ["LNX_RESERVED_PREFIX"], []),
        ("cloud-init-x", ["LNX_RESERVED_PREFIX"], []),
        ("skylab", ["PLATFORM_RESERVED"], []),
        ("ansible", ["PLATFORM_RESERVED"], []),
        ("ubuntu", [], ["LNX_DEFAULT_USER"]),
        ("ec2-user", [], ["LNX_DEFAULT_USER"]),
        # 一次回傳所有違規，不在第一個就停
        ("systemd-journal", ["LNX_RESERVED", "LNX_RESERVED_PREFIX"], []),
        ("Skylab", ["LNX_FORMAT", "PLATFORM_RESERVED"], []),
    ],
)
def test_validate_username(
    name: str, expected_errors: list[str], expected_warnings: list[str]
) -> None:
    violations = validate_username(name)
    assert [v.code for v in violations if v.severity == "error"] == expected_errors
    assert [v.code for v in violations if v.severity == "warning"] == expected_warnings


def test_violation_carries_localized_message() -> None:
    (violation,) = validate_username("admin")
    assert violation.code == "LNX_RESERVED"
    assert violation.severity == "error"
    assert "admin" in violation.message
    assert violation.message != "username_policy.LNX_RESERVED"


def test_no_silent_normalization() -> None:
    """大寫與前後空白直接回報，不會被轉成合法名稱後放行。"""
    assert "LNX_FORMAT" in _codes("JERRY")
    assert "LNX_FORMAT" in _codes("\tjerry")


def test_policy_loaded_from_yaml_as_frozensets() -> None:
    policy = username_policy.POLICY
    assert isinstance(policy.reserved, frozenset)
    assert {"admin", "sudo", "wheel", "root"} <= policy.reserved
    assert policy.platform_reserved == frozenset({"skylab", "ansible", "provision"})
    assert policy.max_length == 32


def test_load_policy_dedupes_and_rejects_bad_file(tmp_path: Path) -> None:
    good = tmp_path / "policy.yaml"
    good.write_text(
        "platform_reserved: [Skylab, skylab]\n"
        "linux:\n"
        "  pattern: '^[a-z]+$'\n"
        "  min_length: 1\n"
        "  max_length: 32\n"
        "  reserved_prefixes: [systemd-, systemd-]\n"
        "  reserved: [admin, admin]\n"
        "  default_user_warn: [ubuntu]\n",
        encoding="utf-8",
    )
    policy = load_policy(good)
    assert policy.reserved == frozenset({"admin"})
    assert policy.reserved_prefixes == ("systemd-",)
    assert policy.platform_reserved == frozenset({"skylab"})

    bad = tmp_path / "bad.yaml"
    bad.write_text("linux:\n  reserved: admin\n", encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        load_policy(bad)


class _Model(BaseModel):
    username: LinuxUsername | None = None


def test_schema_field_rejects_errors_but_allows_warnings() -> None:
    assert _Model(username="jerry").username == "jerry"
    assert _Model(username="ubuntu").username == "ubuntu"
    assert _Model().username is None
    with pytest.raises(ValidationError) as exc:
        _Model(username="Skylab")
    message = str(exc.value)
    # 兩條違規的訊息都要列出
    expected = errors_of(validate_username("Skylab"))
    assert len(expected) == 2
    for violation in expected:
        assert violation.message in message


def test_rules_payload_matches_policy() -> None:
    rules = username_policy.rules()
    assert rules["pattern"] == "^[a-z][a-z0-9_-]{0,31}$"
    assert rules["max_length"] == 32
    assert "admin" in rules["reserved"]
    assert rules["reserved"] == sorted(rules["reserved"])
    assert "systemd-" in rules["reserved_prefixes"]
    assert "ubuntu" in rules["default_user_warn"]
    assert "skylab" in rules["platform_reserved"]
