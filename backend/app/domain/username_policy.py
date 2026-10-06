"""Linux VM 登入帳號（cloud-init ``ciuser``）的命名政策。

規則與保留字清單的唯一來源是 ``backend/config/username_policy.yaml``，import 時
載入並轉成 frozenset；前端經 ``GET /policies/username`` 取得同一份規則，不另寫死。

``validate_username`` 是純函式（不碰 DB／網路），一次回傳所有違規。不做靜默
正規化：大寫、前後空白、全形字元一律回報違規，不自動轉換。

Windows 範本不在範圍內：PVE 對 Windows 會忽略 ``ciuser``，帳號由範本內
cloudbase-init.conf 固定，申請表單本來就不送帳號。

曾發生的事故：Ubuntu 已有 ``admin`` 群組，``ciuser=admin`` 讓 useradd 以 exit 9
失敗，後續 set_passwords／ssh 金鑰也一併失敗，VM 開得起來卻完全無法登入，
PVE 端看不出錯誤 —— 所以要在接受請求時就擋。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AfterValidator

from app.core.i18n import t

Severity = Literal["error", "warning"]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = BACKEND_ROOT / "config" / "username_policy.yaml"


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    severity: Severity = "error"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class UsernamePolicy:
    pattern: re.Pattern[str]
    min_length: int
    max_length: int
    reserved: frozenset[str]
    reserved_prefixes: tuple[str, ...]
    default_user_warn: frozenset[str]
    # 比對時不分大小寫
    platform_reserved: frozenset[str]


def _str_list(section: dict[str, Any], key: str) -> list[str]:
    value = section.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"username_policy.yaml: '{key}' must be a list of strings")
    return value


def load_policy(path: Path = CONFIG_FILE) -> UsernamePolicy:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("username_policy.yaml must be a mapping")
    linux = payload.get("linux")
    if not isinstance(linux, dict):
        raise ValueError("username_policy.yaml needs a 'linux' section")
    return UsernamePolicy(
        pattern=re.compile(str(linux["pattern"])),
        min_length=int(linux["min_length"]),
        max_length=int(linux["max_length"]),
        reserved=frozenset(_str_list(linux, "reserved")),
        reserved_prefixes=tuple(dict.fromkeys(_str_list(linux, "reserved_prefixes"))),
        default_user_warn=frozenset(_str_list(linux, "default_user_warn")),
        platform_reserved=frozenset(
            v.casefold() for v in _str_list(payload, "platform_reserved")
        ),
    )


POLICY = load_policy()


def _v(code: str, severity: Severity = "error", **params: object) -> Violation:
    return Violation(
        code=code,
        message=t(f"username_policy.{code}", **params),
        severity=severity,
    )


def validate_username(name: str, policy: UsernamePolicy = POLICY) -> list[Violation]:
    """檢查單一帳號名稱，回傳所有違規（error 與 warning）；空清單代表通過。"""
    out: list[Violation] = []
    if not policy.pattern.fullmatch(name):
        out.append(_v("LNX_FORMAT", max_length=policy.max_length))
    if name in policy.reserved:
        out.append(_v("LNX_RESERVED", name=name))
    for prefix in policy.reserved_prefixes:
        if name.startswith(prefix):
            out.append(_v("LNX_RESERVED_PREFIX", prefix=prefix))
            break
    if name.casefold() in policy.platform_reserved:
        out.append(_v("PLATFORM_RESERVED", name=name))
    if name in policy.default_user_warn:
        out.append(_v("LNX_DEFAULT_USER", severity="warning", name=name))
    return out


def errors_of(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "error"]


def validate_username_field(value: str | None) -> str | None:
    """Pydantic 欄位驗證：有 error 等級的違規就整批列出擋下，warning 不擋。"""
    if value is None:
        return value
    errors = errors_of(validate_username(value))
    if errors:
        raise ValueError("；".join(v.message for v in errors))
    return value


# schema 共用的 cloud-init 帳號欄位型別（Windows 範本不送帳號，所以只有 Linux 規則）
LinuxUsername = Annotated[str, AfterValidator(validate_username_field)]


def rules(policy: UsernamePolicy = POLICY) -> dict[str, Any]:
    """前端即時驗證需要的規則（GET /policies/username）。"""
    return {
        "pattern": policy.pattern.pattern,
        "min_length": policy.min_length,
        "max_length": policy.max_length,
        "reserved": sorted(policy.reserved),
        "reserved_prefixes": list(policy.reserved_prefixes),
        "platform_reserved": sorted(policy.platform_reserved),
        "default_user_warn": sorted(policy.default_user_warn),
    }


__all__ = [
    "POLICY",
    "LinuxUsername",
    "UsernamePolicy",
    "Violation",
    "errors_of",
    "load_policy",
    "rules",
    "validate_username",
    "validate_username_field",
]
