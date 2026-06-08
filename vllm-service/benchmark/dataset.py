"""
Benchmark 測試資料集模組
支援從 JSON 檔案載入問答集
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TestCase:
    """單一測試案例"""
    id: str
    category: str
    prompt: str
    expected_keywords: list[str] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCase:
        """從字典創建測試案例"""
        return cls(
            id=data.get("id", ""),
            category=data.get("category", "general"),
            prompt=data.get("prompt", ""),
            expected_keywords=data.get("expected_keywords"),
            max_tokens=data.get("max_tokens"),
            temperature=data.get("temperature"),
            metadata=data.get("metadata"),
        )


@dataclass
class TestDataset:
    """測試資料集"""
    name: str
    description: str
    version: str
    test_cases: list[TestCase]

    @classmethod
    def from_json(cls, filepath: str | Path) -> TestDataset:
        """從 JSON 檔案載入資料集"""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"測試資料集不存在: {filepath}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        test_cases = [
            TestCase.from_dict(case)
            for case in data.get("test_cases", [])
        ]

        return cls(
            name=data.get("name", "Unnamed Dataset"),
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            test_cases=test_cases,
        )

    def filter_by_category(self, category: str) -> list[TestCase]:
        """依類別篩選測試案例"""
        return [tc for tc in self.test_cases if tc.category == category]

    def get_categories(self) -> list[str]:
        """取得所有類別"""
        categories = {tc.category for tc in self.test_cases}
        return sorted(categories)

    def __len__(self) -> int:
        return len(self.test_cases)


def load_dataset(filepath: str | Path) -> TestDataset:
    """便利函式：載入測試資料集"""
    return TestDataset.from_json(filepath)
