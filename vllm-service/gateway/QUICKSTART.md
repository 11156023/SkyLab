# API Gateway 快速啟動指南

## 1. 準備設定

```bash
cd vllm-service
cp .env.example .env
cp models.json.example models.json
```

依實際環境編輯：

- `.env`：`API_KEY`、`GATEWAY_HOST`、`GATEWAY_PORT`、`HF_CACHE_DIR`
- `models.json`：模型 `alias`、`model_name`、`api_port`、GPU 配額

## 2. 安裝依賴

```bash
pip install -r requirements.txt
```

`vllm` 需依 GPU/CUDA/平台版本另外安裝。

## 3. 啟動多模型 Gateway

```bash
python main.py gateway
```

或使用薄封裝腳本：

```bash
bash ./start_multi_model_gateway.sh
```

Gateway 預設位於 `http://localhost:3000`。

## 4. 驗證

```bash
curl http://localhost:3000/health
curl http://localhost:3000/v1/models \
  -H "Authorization: Bearer vllm-secret-key-change-me"
```

Chat completion：

```bash
curl http://localhost:3000/v1/chat/completions \
  -H "Authorization: Bearer vllm-secret-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-14b",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

## 常見問題

### Gateway 無法啟動

檢查 `models.json` 的 `api_port` 是否重複或已被其他程序佔用。

### `/ready` 回傳 503

代表至少一個上游模型尚未 ready。查看 `logs/` 內的 vLLM instance 與 Gateway 日誌。

### `/v1/models` 沒有模型

確認 `models.json` 有有效 alias，且 Gateway 使用同一份設定啟動。

### 主 backend 無法呼叫 Gateway

確認主專案 `.env`：

```env
AI_API_BASE_URL=http://localhost:3000
AI_API_API_KEY=vllm-secret-key-change-me
```
