"""
日誌工具 - 統一的日誌管理
提供顏色輸出和結構化日誌記錄
"""

from __future__ import annotations

import sys
from datetime import datetime
from enum import Enum


class LogLevel(Enum):
    """日誌級別"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


# ANSI 顏色碼
COLORS = {
    LogLevel.DEBUG: "\033[36m",      # Cyan
    LogLevel.INFO: "\033[37m",       # White
    LogLevel.WARNING: "\033[33m",    # Yellow
    LogLevel.ERROR: "\033[31m",      # Red
    LogLevel.SUCCESS: "\033[32m",    # Green
}
RESET = "\033[0m"
BOLD = "\033[1m"


class Logger:
    """簡單的彩色日誌記錄器"""
    
    def __init__(self, name: str = "vLLM", enable_color: bool = True) -> None:
        self.name = name
        self.enable_color = enable_color and sys.stdout.isatty()
    
    def _format_message(
        self, 
        level: LogLevel, 
        message: str, 
        prefix: str | None = None
    ) -> str:
        """格式化日誌訊息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if prefix is None:
            prefix = self.name
        
        if self.enable_color:
            color = COLORS.get(level, "")
            level_str = f"{color}{BOLD}[{level.value}]{RESET}"
            prefix_str = f"{BOLD}[{prefix}]{RESET}"
            return f"{timestamp} {level_str} {prefix_str} {message}"
        else:
            return f"{timestamp} [{level.value}] [{prefix}] {message}"
    
    def debug(self, message: str, prefix: str | None = None) -> None:
        """調試日誌"""
        print(self._format_message(LogLevel.DEBUG, message, prefix))
    
    def info(self, message: str, prefix: str | None = None) -> None:
        """資訊日誌"""
        print(self._format_message(LogLevel.INFO, message, prefix))
    
    def warning(self, message: str, prefix: str | None = None) -> None:
        """警告日誌"""
        print(self._format_message(LogLevel.WARNING, message, prefix), file=sys.stderr)
    
    def error(self, message: str, prefix: str | None = None) -> None:
        """錯誤日誌"""
        print(self._format_message(LogLevel.ERROR, message, prefix), file=sys.stderr)
    
    def success(self, message: str, prefix: str | None = None) -> None:
        """成功日誌"""
        print(self._format_message(LogLevel.SUCCESS, message, prefix))
    
    def section(self, title: str, width: int = 60) -> None:
        """輸出分隔線"""
        if self.enable_color:
            print(f"\n{BOLD}{'='*width}")
            print(f"  {title}")
            print(f"{'='*width}{RESET}\n")
        else:
            print(f"\n{'='*width}")
            print(f"  {title}")
            print(f"{'='*width}\n")



def get_logger(name: str = "vLLM") -> Logger:
    """獲取日誌記錄器"""
    return Logger(name)


def log_system_info() -> None:
    """輸出系統資訊"""
    import platform
    import psutil
    
    logger = get_logger("System")
    logger.section("系統資訊")
    logger.info(f"作業系統: {platform.system()} {platform.release()}")
    logger.info(f"Python 版本: {platform.python_version()}")
    logger.info(f"CPU 核心數: {psutil.cpu_count(logical=False)} 實體 / {psutil.cpu_count(logical=True)} 邏輯")
    
    # 記憶體
    mem = psutil.virtual_memory()
    logger.info(f"總記憶體: {mem.total / (1024**3):.1f} GB")
    logger.info(f"可用記憶體: {mem.available / (1024**3):.1f} GB ({mem.percent}% 已使用)")
    
    # GPU 資訊（如果可用）
    try:
        import torch
        if torch.cuda.is_available():
            logger.info(f"CUDA 版本: {torch.version.cuda}")
            logger.info(f"GPU 數量: {torch.cuda.device_count()}")
            for i in range(torch.cuda.device_count()):
                gpu_name = torch.cuda.get_device_name(i)
                gpu_mem = torch.cuda.get_device_properties(i).total_memory / (1024**3)
                logger.info(f"  GPU {i}: {gpu_name} ({gpu_mem:.1f} GB)")
        else:
            logger.warning("未檢測到 CUDA GPU")
    except ImportError:
        logger.warning("PyTorch 未安裝，無法檢測 GPU")


# ============================================================
# 測試
# ============================================================

if __name__ == "__main__":
    logger = get_logger("Test")
    
    logger.section("日誌工具測試")
    logger.debug("這是調試訊息")
    logger.info("這是資訊訊息")
    logger.warning("這是警告訊息")
    logger.error("這是錯誤訊息")
    logger.success("這是成功訊息")
    
    print()
    log_system_info()
