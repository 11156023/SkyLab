"""
ShareGPT Benchmark 執行入口
使用 ShareGPT 數據集進行性能測試
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark.sharegpt_bench import main

if __name__ == "__main__":
    main()
