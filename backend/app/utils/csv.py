"""CSV 輸出共用安全處理。"""

# 試算表會把以這些字元開頭的儲存格當成公式；全形版本是 CJK 輸入法常見的
# 變體，也必須一併處理。
CSV_FORMULA_PREFIXES = (
    "=",
    "+",
    "-",
    "@",
    "\t",
    "\r",
    "＝",
    "＋",
    "－",
    "＠",
)


def csv_safe(value: str | None) -> str:
    """Neutralise spreadsheet formula injection in a free-text CSV cell."""
    if not value:
        return ""
    if value.lstrip(" ").startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


# 保留內部測試與舊呼叫端可使用的名稱；新程式碼優先使用 public helper。
_csv_safe = csv_safe


__all__ = ["CSV_FORMULA_PREFIXES", "csv_safe"]
