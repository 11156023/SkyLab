"""
健康檢查工具 - 系統資源和模型狀態監控
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


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
    
    # GPU
    gpu_count = 0
    gpu_memory_used_gb = []
    gpu_memory_total_gb = []
    gpu_utilization = []
    
    try:
        import torch
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            for i in range(gpu_count):
                # 記憶體
                mem_used = torch.cuda.memory_allocated(i) / (1024 ** 3)
                mem_total = torch.cuda.get_device_properties(i).total_memory / (1024 ** 3)
                gpu_memory_used_gb.append(mem_used)
                gpu_memory_total_gb.append(mem_total)
                
                # 使用率（需要 nvidia-ml-py 或 pynvml）
                try:
                    import pynvml
                    pynvml.nvmlInit()
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_utilization.append(util.gpu)
                except (ImportError, Exception):
                    gpu_utilization.append(0.0)
    except (ImportError, Exception):
        pass
    
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


def check_vllm_endpoint(base_url: str, api_key: str, timeout: int = 5) -> dict[str, Any]:
    """檢查 vLLM 端點狀態"""
    result = {
        "health": False,
        "models": [],
        "error": None,
    }
    
    try:
        # 檢查 health 端點
        health_url = f"{base_url}/health"
        resp = httpx.get(health_url, timeout=timeout)
        result["health"] = (resp.status_code == 200)
        
        # 檢查 models 端點
        if result["health"]:
            models_url = f"{base_url}/v1/models"
            resp = httpx.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                result["models"] = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        result["error"] = str(e)
    
    return result


def suggest_cache_cleanup(cache_dir: str | Path) -> dict[str, Any]:
    """建議快取清理"""
    cache_path = Path(cache_dir)
    
    if not cache_path.exists():
        return {
            "exists": False,
            "message": f"快取目錄不存在: {cache_dir}",
        }
    
    # 計算快取大小
    total_size = 0
    file_count = 0
    
    for item in cache_path.rglob("*"):
        if item.is_file():
            total_size += item.stat().st_size
            file_count += 1
    
    total_size_gb = total_size / (1024 ** 3)
    
    # 判斷是否需要清理
    needs_cleanup = total_size_gb > 100  # 超過 100GB
    
    return {
        "exists": True,
        "path": str(cache_path),
        "total_size_gb": round(total_size_gb, 2),
        "file_count": file_count,
        "needs_cleanup": needs_cleanup,
        "suggestion": (
            f"快取目錄佔用 {total_size_gb:.1f} GB，建議清理舊模型" 
            if needs_cleanup 
            else f"快取目錄佔用 {total_size_gb:.1f} GB，無需清理"
        ),
    }


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
    
    # 快取檢查示例
    logger.section("快取狀態")
    cache_info = suggest_cache_cleanup("/raid/hf-cache/hub")
    if cache_info["exists"]:
        logger.info(f"快取目錄: {cache_info['path']}")
        logger.info(f"總大小: {cache_info['total_size_gb']} GB")
        logger.info(f"檔案數: {cache_info['file_count']}")
        logger.info(cache_info['suggestion'])
    else:
        logger.warning(cache_info['message'])
