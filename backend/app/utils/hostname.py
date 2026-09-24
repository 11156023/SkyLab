import logging
import unicodedata
from typing import Annotated

from pydantic import AfterValidator

logger = logging.getLogger(__name__)


def validate_unicode_hostname(v: str) -> str:
    """驗證 hostname：允許 Unicode 字母/數字和連字符，並檢查 Punycode 編碼後長度。"""
    if not v:
        raise ValueError("Hostname cannot be empty")
    if v.startswith("-") or v.endswith("-"):
        raise ValueError("Hostname cannot start or end with a hyphen")
    for ch in v:
        if ch == "-":
            continue
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat.startswith("N")):
            raise ValueError(
                "Only Unicode letters, digits, and hyphens are allowed in hostname"
            )
    # 檢查 Punycode 編碼後的長度是否仍在 DNS label 限制內（≤ 63 字元）
    try:
        encoded = v.encode("punycode").decode("ascii")
        # 如果包含非 ASCII 字元，實際 DNS label 會加上 "xn--" 前綴
        ace_label = v if v.isascii() else f"xn--{encoded}"
        if len(ace_label) > 63:
            raise ValueError(
                f"Hostname exceeds 63 characters after Punycode encoding "
                f"(encoded length: {len(ace_label)})"
            )
    except UnicodeError as e:
        raise ValueError(f"Hostname cannot be encoded as valid Punycode: {e}") from e
    return v


# resource / vm_request schema 共用的 hostname 欄位型別
UnicodeHostname = Annotated[str, AfterValidator(validate_unicode_hostname)]


def to_punycode_hostname(hostname: str) -> str:
    """將 Unicode hostname 轉換為 Punycode（ACE 格式）傳給 PVE。"""
    if not isinstance(hostname, str):
        logger.error(
            "Expected str for hostname, got %s: %r", type(hostname).__name__, hostname
        )
        raise TypeError(
            f"hostname must be str, got {type(hostname).__name__!r}: {hostname!r}"
        )

    result_labels = []
    for label in hostname.split("."):
        if not label:
            raise ValueError("Hostname labels must not be empty")
        try:
            label.encode("ascii")
            ace = label
        except UnicodeEncodeError:
            try:
                ace = "xn--" + label.encode("punycode").decode("ascii")
            except Exception as exc:
                raise ValueError(
                    f"Cannot encode hostname label '{label}' to Punycode: {exc}"
                ) from exc

        if len(ace) > 63:
            raise ValueError(
                f"Encoded hostname label '{label}' exceeds 63 characters after Punycode conversion"
            )
        result_labels.append(ace)

    encoded_hostname = ".".join(result_labels)
    if len(encoded_hostname) > 253:
        raise ValueError(
            "Encoded hostname exceeds 253 characters after Punycode conversion"
        )
    return encoded_hostname
