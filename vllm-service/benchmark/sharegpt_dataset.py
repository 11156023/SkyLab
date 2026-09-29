"""
ShareGPT 數據集模組
支援載入和解析 ShareGPT 格式的對話數據集
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ShareGPTConversation:
    """ShareGPT 對話案例"""
    id: str
    conversations: list[dict[str, str]]  # [{"from": "human/gpt", "value": "..."}]
    
    @property
    def prompt(self) -> str:
        """獲取用戶提示（第一個 human 訊息）"""
        for conv in self.conversations:
            if conv.get("from") == "human":
                return conv.get("value", "")
        return ""
    
    @property
    def num_turns(self) -> int:
        """對話輪數"""
        return len(self.conversations) // 2
    
    @classmethod
    def from_dict(cls, data: dict[str, Any], conv_id: str | None = None) -> ShareGPTConversation:
        """從字典創建對話案例"""
        return cls(
            id=conv_id or data.get("id", "unknown"),
            conversations=data.get("conversations", []),
        )


@dataclass
class ShareGPTDataset:
    """ShareGPT 測試資料集"""
    conversations: list[ShareGPTConversation]
    name: str = "ShareGPT"
    
    @classmethod
    def from_json(cls, filepath: str | Path) -> ShareGPTDataset:
        """從 JSON 檔案載入資料集"""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"測試資料集不存在: {filepath}")
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # ShareGPT 格式是一個對話列表
        if not isinstance(data, list):
            raise ValueError("ShareGPT 數據集應該是一個列表")
        
        conversations = []
        for idx, item in enumerate(data):
            conv_id = item.get("id", f"conv_{idx}")
            try:
                conv = ShareGPTConversation.from_dict(item, conv_id)
                # 只保留有效的對話（至少有一個 human 訊息）
                if conv.prompt:
                    conversations.append(conv)
            except Exception as e:
                print(f"[Warning] 跳過無效對話 {conv_id}: {e}")
                continue
        
        return cls(
            conversations=conversations,
            name=f"ShareGPT ({path.name})",
        )
    
    def sample(self, n: int, seed: int | None = None) -> list[ShareGPTConversation]:
        """隨機採樣 n 個對話"""
        # 用獨立的 Random，避免改動全域亂數狀態
        n = min(n, len(self.conversations))
        return random.Random(seed).sample(self.conversations, n)
    
    def __len__(self) -> int:
        return len(self.conversations)
    
    def __getitem__(self, idx: int) -> ShareGPTConversation:
        return self.conversations[idx]


def load_sharegpt_dataset(filepath: str | Path) -> ShareGPTDataset:
    """便利函式：載入 ShareGPT 資料集"""
    return ShareGPTDataset.from_json(filepath)


def download_sharegpt_dataset(output_path: str | Path = "ShareGPT_V3_unfiltered_cleaned_split.json") -> Path:
    """
    下載 ShareGPT_V3 數據集
    
    Args:
        output_path: 輸出檔案路徑
        
    Returns:
        下載的檔案路徑
    """
    import urllib.request
    
    url = "https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json"
    output_path = Path(output_path)
    
    if output_path.exists():
        print(f"[ShareGPT] 數據集已存在: {output_path}")
        return output_path
    
    print("[ShareGPT] 下載數據集...")
    print(f"[ShareGPT] URL: {url}")
    print(f"[ShareGPT] 輸出: {output_path}")

    # 預設路徑 test_datasets/ 不在版控內，先建立目錄；先下載到 .part 再改名，
    # 中斷的下載不會留下半個檔案被下次的「已存在」檢查誤用。
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(output_path.name + ".part")
    try:
        urllib.request.urlretrieve(url, partial_path)  # nosec B310 - 固定的 HuggingFace 資料集 URL
        partial_path.replace(output_path)
    finally:
        partial_path.unlink(missing_ok=True)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[ShareGPT] 下載完成: {size_mb:.1f} MB")

    return output_path
