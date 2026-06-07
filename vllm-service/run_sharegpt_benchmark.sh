#!/usr/bin/env bash
# ============================================================
# ShareGPT Benchmark 快速啟動腳本
# 使用 ShareGPT 數據集進行性能測試
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 顏色定義
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# 啟用虛擬環境
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "  ${GREEN}🚀 ShareGPT vLLM Benchmark${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# 預設設定
DEFAULT_DATASET="test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json"
DATASET_URL="https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json"

print_help() {
    echo -e "${YELLOW}使用方式:${NC}"
    echo "  $0 [選項]"
    echo ""
    echo -e "${YELLOW}無參數模式:${NC}"
    echo "  進入詢問式流程，只詢問："
    echo "    1. 壓測 API Gateway 或主服務"
    echo "    2. 模型 alias / 模型名稱"
    echo "    3. 測試筆數"
    echo "    4. 併發數"
    echo ""
    echo "  其他設定固定：ShareGPT 預設測試集、settings.py 的 max tokens、temperature=0.7、seed=42、儲存報告。"
    echo ""
    echo -e "${YELLOW}選項:${NC}"
    echo "  --interactive           強制進入詢問式流程"
    echo "  --download              下載 ShareGPT_V3 數據集"
    echo "  -d, --dataset PATH      進階：ShareGPT 數據集路徑 (默認: ${DEFAULT_DATASET})"
    echo "  -n, --num-samples N     進階：採樣數量 (不指定則使用全部)"
    echo "  -c, --concurrency N     進階：併發數"
    echo "  -m, --max-tokens N      進階：每次最大生成 token 數"
    echo "  -t, --temperature T     進階：溫度參數 (默認: 0.7)"
    echo "  --seed N                進階：隨機種子 (默認: 42)"
    echo "  --no-save               進階：不儲存報告"
    echo "  -h, --help              顯示此幫助訊息"
    echo ""
    echo -e "${YELLOW}範例:${NC}"
    echo ""
    echo "  # 詢問式流程"
    echo "  $0"
    echo ""
    echo "  # 下載 ShareGPT 數據集"
    echo "  $0 --download"
    echo ""
    echo "  # 進階：快速測試 (100 個樣本)"
    echo "  $0 -n 100 -c 20"
    echo ""
    echo "  # 完整測試 (預設採樣 1000 個)"
    echo "  $0 -n 1000 -c 50"
    echo ""
    echo "  # 大規模測試 (5000 個樣本，高併發)"
    echo "  $0 -n 5000 -c 100"
    echo ""
    echo "  # 自定義數據集"
    echo "  $0 -d /path/to/custom_sharegpt.json -n 500"
    echo ""
}

# 檢查參數
if [[ $# -eq 0 ]]; then
    echo -e "${CYAN}▶${NC} 無參數模式：進入互動式 Benchmark..."
    echo ""
    exec python3 run_sharegpt_benchmark.py --interactive
fi

# 解析參數
DATASET=""
NUM_SAMPLES=""
CONCURRENCY=""
MAX_TOKENS=""
TEMPERATURE="0.7"
SEED="42"
NO_SAVE=""
DOWNLOAD_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dataset)
            DATASET="$2"
            shift 2
            ;;
        -n|--num-samples)
            NUM_SAMPLES="$2"
            shift 2
            ;;
        -c|--concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        -m|--max-tokens)
            MAX_TOKENS="$2"
            shift 2
            ;;
        -t|--temperature)
            TEMPERATURE="$2"
            shift 2
            ;;
        --seed)
            SEED="$2"
            shift 2
            ;;
        --no-save)
            NO_SAVE="--no-save"
            shift
            ;;
        --download)
            DOWNLOAD_ONLY=true
            shift
            ;;
        --interactive)
            exec python3 run_sharegpt_benchmark.py --interactive
            ;;
        -h|--help)
            print_help
            exit 0
            ;;
        *)
            echo -e "${YELLOW}未知選項: $1${NC}"
            exit 1
            ;;
    esac
done

# 如果只是下載數據集
if [[ "$DOWNLOAD_ONLY" = true ]]; then
    echo -e "${CYAN}▶${NC} 下載 ShareGPT_V3 數據集..."
    echo ""
    
    if [[ -f "$DEFAULT_DATASET" ]]; then
        echo -e "${GREEN}✓${NC} 數據集已存在: $DEFAULT_DATASET"
        SIZE=$(du -h "$DEFAULT_DATASET" | cut -f1)
        echo "  大小: $SIZE"
    else
        echo "  URL: $DATASET_URL"
        echo "  輸出: $DEFAULT_DATASET"
        echo ""
        
        if command -v wget &> /dev/null; then
            wget -O "$DEFAULT_DATASET" "$DATASET_URL"
        elif command -v curl &> /dev/null; then
            curl -L -o "$DEFAULT_DATASET" "$DATASET_URL"
        else
            echo -e "${YELLOW}⚠${NC} 需要 wget 或 curl 來下載數據集"
            exit 1
        fi
        
        echo ""
        echo -e "${GREEN}✓${NC} 下載完成"
        SIZE=$(du -h "$DEFAULT_DATASET" | cut -f1)
        echo "  大小: $SIZE"
    fi
    
    echo ""
    echo "使用此數據集運行 benchmark:"
    echo "  $0 -d $DEFAULT_DATASET -n 100 -c 20"
    echo ""
    exit 0
fi

# 設定默認數據集
if [[ -z "$DATASET" ]]; then
    DATASET="$DEFAULT_DATASET"
fi

# 檢查數據集是否存在
if [[ ! -f "$DATASET" ]]; then
    echo -e "${YELLOW}⚠${NC} 數據集不存在: $DATASET"
    echo ""
    echo "下載 ShareGPT_V3 數據集:"
    echo "  $0 --download"
    echo ""
    exit 1
fi

# 顯示配置
echo -e "${CYAN}▶${NC} 配置:"
echo "  數據集:         $DATASET"
[[ -n "$NUM_SAMPLES" ]] && echo "  採樣數量:       $NUM_SAMPLES"
[[ -n "$CONCURRENCY" ]] && echo "  併發數:         $CONCURRENCY"
[[ -n "$MAX_TOKENS" ]] && echo "  最大 Token:     $MAX_TOKENS"
echo "  溫度:           $TEMPERATURE"
echo "  隨機種子:       $SEED"
echo ""

# 構建命令
CMD="python3 run_sharegpt_benchmark.py \"$DATASET\""
[[ -n "$NUM_SAMPLES" ]] && CMD="$CMD -n $NUM_SAMPLES"
[[ -n "$CONCURRENCY" ]] && CMD="$CMD -c $CONCURRENCY"
[[ -n "$MAX_TOKENS" ]] && CMD="$CMD -m $MAX_TOKENS"
CMD="$CMD -t $TEMPERATURE --seed $SEED $NO_SAVE"

# 執行
echo -e "${CYAN}▶${NC} 啟動 Benchmark..."
echo ""

eval $CMD

echo ""
echo -e "${GREEN}✓${NC} Benchmark 完成"
echo ""
