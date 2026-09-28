"""
健康檢查工具 - 系統資源和模型狀態監控
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SystemHealth:
    """系統健康狀態"""
    cpu_percent: float
    memory_percent: float
    memory_available_gb: float
    disk_usage_percent: float
    gpu_count: int
    gpu_memory_used_gb: list[float]
    gpu_memory_total_gb: list[float]
    gpu_utilization: list[float]
    
    def is_healthy(self) -> bool:
        """判斷系統是否健康"""
        # CPU 使用率不應持續超過 95%
        if self.cpu_percent > 95:
            return False
        
        # 記憶體使用率不應超過 95%
        if self.memory_percent > 95:
            return False
        
        # 可用記憶體至少 1GB
        if self.memory_available_gb < 1.0:
            return False
        
        # 磁碟使用率不應超過 95%
        if self.disk_usage_percent > 95:
            return False
        
        # 所有 GPU 記憶體使用率不應超過 98%
        for used, total in zip(self.gpu_memory_used_gb, self.gpu_memory_total_gb):
            if total > 0 and (used / total) > 0.98:
                return False
        
        return True
    
    def get_warnings(self) -> list[str]:
        """獲取健康警告"""
        warnings = []
        
        if self.cpu_percent > 90:
            warnings.append(f"CPU 使用率過高: {self.cpu_percent:.1f}%")
        
        if self.memory_percent > 90:
            warnings.append(f"記憶體使用率過高: {self.memory_percent:.1f}%")
        
        if self.memory_available_gb < 2.0:
            warnings.append(f"可用記憶體過低: {self.memory_available_gb:.1f} GB")
        
        if self.disk_usage_percent > 90:
            warnings.append(f"磁碟使用率過高: {self.disk_usage_percent:.1f}%")
        
        for i, (used, total) in enumerate(zip(self.gpu_memory_used_gb, self.gpu_memory_total_gb)):
            if total > 0:
                usage_percent = (used / total) * 100
                if usage_percent > 95:
                    warnings.append(f"GPU {i} 記憶體使用率過高: {usage_percent:.1f}%")
        
        return warnings


_GIB = 1024 ** 3


def _collect_gpu_stats() -> tuple[int, list[float], list[float], list[float]]:
    """回傳 (GPU 數, 已用 GB, 總量 GB, 使用率 %)，數字為整張卡的實際用量。

    優先用 NVML：它讀的是驅動層的整卡用量（含其他 vLLM 實例或工作），
    而且不會在 launcher 建立 CUDA context。torch.cuda.memory_allocated 只算
    本程序的配置（launcher 永遠是 0），torch.cuda.mem_get_info 則會在每張卡
    建立 CUDA context、長期佔用數百 MB 顯存，兩者都不適合這裡。
    NVML 不可用時退回 torch 只取卡數與總量；已用量未知時記為 0。
    """
    try:
        import pynvml

        pynvml.nvmlInit()
    except Exception:
        return _collect_gpu_stats_from_torch()

    used_gb: list[float] = []
    total_gb: list[float] = []
    utilization: list[float] = []
    try:
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            used_gb.append(memory.used / _GIB)
            total_gb.append(memory.total / _GIB)
            try:
                utilization.append(float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu))
            except Exception:
                utilization.append(0.0)
    except Exception:
        return _collect_gpu_stats_from_torch()
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
    return count, used_gb, total_gb, utilization


def _collect_gpu_stats_from_torch() -> tuple[int, list[float], list[float], list[float]]:
    """NVML 不可用時的退路：只有卡數與總顯存可信，已用量與使用率未知。"""
    try:
        import torch

        if not torch.cuda.is_available():
            return 0, [], [], []
        count = torch.cuda.device_count()
        total_gb = [
            torch.cuda.get_device_properties(i).total_memory / _GIB for i in range(count)
        ]
    except Exception:
        return 0, [], [], []
    return count, [0.0] * count, total_gb, [0.0] * count


def check_system_health() -> SystemHealth:
    """檢查系統健康狀態"""
    import psutil
    
    # CPU 和記憶體
    cpu_percent = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    memory_percent = mem.percent
    memory_available_gb = mem.available / (1024 ** 3)
    
    # 磁碟
    disk = psutil.disk_usage('/')
    disk_usage_percent = disk.percent
    
    gpu_count, gpu_memory_used_gb, gpu_memory_total_gb, gpu_utilization = _collect_gpu_stats()

    return SystemHealth(
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        memory_available_gb=memory_available_gb,
        disk_usage_percent=disk_usage_percent,
        gpu_count=gpu_count,
        gpu_memory_used_gb=gpu_memory_used_gb,
        gpu_memory_total_gb=gpu_memory_total_gb,
        gpu_utilization=gpu_utilization,
    )


# ============================================================
# 測試
# ============================================================

if __name__ == "__main__":
    from utils.logging_utils import get_logger
    
    logger = get_logger("Health")
    
    logger.section("系統健康檢查")
    
    # 檢查系統健康
    health = check_system_health()
    
    logger.info(f"CPU 使用率: {health.cpu_percent:.1f}%")
    logger.info(f"記憶體使用率: {health.memory_percent:.1f}%")
    logger.info(f"可用記憶體: {health.memory_available_gb:.1f} GB")
    logger.info(f"磁碟使用率: {health.disk_usage_percent:.1f}%")
    
    if health.gpu_count > 0:
        logger.info(f"GPU 數量: {health.gpu_count}")
        for i in range(health.gpu_count):
            used = health.gpu_memory_used_gb[i]
            total = health.gpu_memory_total_gb[i]
            util = health.gpu_utilization[i]
            logger.info(f"  GPU {i}: {used:.1f}/{total:.1f} GB ({util:.1f}% 使用率)")
    
    # 檢查健康狀態
    if health.is_healthy():
        logger.success("系統健康狀態良好")
    else:
        logger.warning("系統健康狀態異常")
        for warning in health.get_warnings():
            logger.warning(f"  - {warning}")

