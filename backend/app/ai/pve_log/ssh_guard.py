"""SSH 指令安全守衛 — 黑名單過濾層

設計原則：
  - 黑名單匹配到 → 立即回傳 blocked=True，不執行
  - 黑名單未匹配 → 允許進入下一層（執行前確認）
  - 使用 re.search 全文匹配（不依賴字首），避免繞過
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 黑名單規則
# ---------------------------------------------------------------------------

_BLACKLIST_RULES: list[tuple[str, str]] = [
    # (pattern, 中文說明)
    (r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r", "遞迴強制刪除（rm -rf）"),
    (r"\bmkfs\b", "格式化磁碟（mkfs）"),
    (r"\bdd\b.+of=/dev/", "磁碟覆寫（dd ... of=/dev/）"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", "關機或重啟指令"),
    (r"passwd\s+\w", "修改帳號密碼（passwd）"),
    (r":(){:|:&};:", "Fork Bomb"),
    (r"chmod\s+[0-9]*7[0-9]*7\s+/\s*$|chmod\s+-R.+777\s+/", "危險全域權限設定"),
    (r">\s*/dev/(sda|sdb|sdc|nvme|vda|vdb)", "直接寫入磁碟設備"),
    (r"curl\s+.+\|\s*(ba)?sh|wget\s+.+\|\s*(ba)?sh", "下載後直接執行腳本"),
    (r"\bsystemctl\s+(stop|disable|mask)\s+(sshd|ssh|network|networking)", "停用 SSH 或網路服務"),
    (r"iptables\s+-F|ufw\s+--force\s+reset|nft\s+flush", "清空防火牆規則"),
    # 下載到檔案再執行、寫入 authorized_keys、反向 shell：curl|sh 之外最常見的植入手法
    (r"(curl|wget)\s+.*-[oO]\s*\S+.*(;|&&|\|\|)\s*(ba)?sh\b", "下載檔案後執行"),
    (r">>?\s*~?/?(root|home/[^/\s]+)?/?\.ssh/authorized_keys", "寫入 SSH 授權金鑰"),
    (r"\b(nc|ncat|netcat)\b.*\s-e\s|/dev/tcp/", "反向 shell"),
    (r"\b(useradd|adduser|usermod)\b", "新增或修改系統帳號"),
    (r"\bcrontab\s+-|/etc/cron", "修改排程"),
    (r"\bchmod\s+[ug]?\+s\b|\bchmod\s+[0-9]*[4-7][0-9]{3}\b", "設定 setuid/setgid"),
    (r"\b(base64|xxd)\s+(-d|--decode|-r)\b.*\|\s*(ba)?sh\b", "解碼後執行"),
    (r"\$\{IFS\}|\$IFS", "利用 IFS 拆解指令"),
]

_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat, re.IGNORECASE), desc) for pat, desc in _BLACKLIST_RULES
]


# ---------------------------------------------------------------------------
# 資料類別
# ---------------------------------------------------------------------------


@dataclass
class GuardResult:
    allowed: bool
    reason: str | None = None  # 若 allowed=False，說明被哪條規則攔截


# ---------------------------------------------------------------------------
# 主要函式
# ---------------------------------------------------------------------------


def check_command(command: str) -> GuardResult:
    """檢查指令是否符合黑名單。

    Returns:
        GuardResult(allowed=True)  → 指令安全，可進入下一層
        GuardResult(allowed=False, reason=...) → 指令被攔截
    """
    cmd = command.strip()
    for pattern, desc in _COMPILED:
        if pattern.search(cmd):
            return GuardResult(
                allowed=False,
                reason=f"指令含有危險操作（{desc}），已被安全機制攔截。",
            )
    return GuardResult(allowed=True)
