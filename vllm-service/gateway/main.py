"""
FastAPI API Gateway service for vLLM-compatible model routing.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx
import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel

# 導入專案的 API 客戶端
sys.path.append(str(Path(__file__).resolve().parent.parent))

from api.client import ModelClient  # noqa: E402
from config.multi_model import (
    GATEWAY_ENV_FILE_VAR,
    GatewayRoute,
    build_gateway_routes,
    find_route_for_model,
    get_available_models_help,
    load_gateway_config,
    load_model_instances,
)  # noqa: E402
from config.settings import get_settings  # noqa: E402

logger = logging.getLogger(__name__)

# 初始化
app = FastAPI(title="vLLM API Gateway", version="1.0.0")
gateway_env_file = os.getenv(GATEWAY_ENV_FILE_VAR, ".env.API")
settings = get_settings(gateway_env_file)
client = ModelClient(settings)

# 多模型 Gateway 設定（若設定檔缺失則回退單模型）
try:
    _gateway_cfg = load_gateway_config(gateway_env_file)
    _gateway_instances = load_model_instances(gateway_env_file)
    gateway_routes: dict[str, GatewayRoute] = build_gateway_routes(_gateway_instances)
    gateway_default_model = _gateway_cfg.default_model or next(iter(gateway_routes))
    gateway_host = _gateway_cfg.host
    gateway_port = _gateway_cfg.port
    gateway_request_timeout = _gateway_cfg.request_timeout
    gateway_max_inflight = _gateway_cfg.max_inflight
except Exception as exc:
    logger.warning("Gateway 多模型設定載入失敗，回退單模型路由: %s", exc)
    gateway_routes = {
        "default": GatewayRoute(
            alias="default",
            model_name=settings.resolved_model_path,
            base_url=f"http://127.0.0.1:{settings.api_port}/v1",
            api_key=settings.api_key,
        )
    }
    gateway_default_model = "default"
    gateway_host = "0.0.0.0"
    gateway_port = 3000
    gateway_request_timeout = settings.request_timeout
    gateway_max_inflight = 32

gateway_http_client = httpx.AsyncClient(
    timeout=gateway_request_timeout,
    limits=httpx.Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=30.0),
)
gateway_semaphore = asyncio.Semaphore(gateway_max_inflight)

# 模型列表快取（60秒有效期）
_models_cache: dict | None = None
_models_cache_time: float = 0
_MODELS_CACHE_TTL = 60.0  # 秒

# ============================================================
# 檔案驗證常數與輔助函數
# ============================================================

# 檔案大小限制 (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes

# 允許的檔案副檔名
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt"}


async def validate_file_size(file: UploadFile, max_size: int = MAX_FILE_SIZE) -> None:
    """驗證上傳檔案大小"""
    # 讀取檔案以檢查大小
    content = await file.read()
    await file.seek(0)  # 重置檔案指標以供後續使用
    
    if len(content) > max_size:
        size_mb = len(content) / (1024 * 1024)
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"檔案過大 ({size_mb:.1f}MB)，最大允許 {max_mb:.0f}MB"
        )


def validate_file_extension(filename: str | None, allowed_extensions: set[str]) -> str:
    """驗證檔案副檔名並返回"""
    if not filename:
        raise HTTPException(status_code=400, detail="缺少檔案名稱")
    
    suffix = Path(filename).suffix.lower()
    if not suffix or suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise HTTPException(
            status_code=400,
            detail=f"不支援的檔案格式 '{suffix}'，允許的格式: {allowed}"
        )
    return suffix


async def validate_image_content(file_bytes: bytes) -> None:
    """驗證圖片內容（使用 Pillow 驗證真實格式）"""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()  # 驗證圖片完整性
    except Exception:
        logger.exception("圖片檔案驗證失敗")
        raise HTTPException(
            status_code=400,
            detail="無效的圖片檔案"
        )


def safe_remove_temp_file(path: str | None) -> None:
    """安全地刪除臨時檔案，記錄錯誤但不拋出異常"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
            logger.debug(f"已刪除臨時檔案: {path}")
        except OSError as e:
            logger.error(f"無法刪除臨時檔案 {path}: {e}")


async def write_upload_to_temp_async(upload_file: UploadFile, suffix: str, prefix: str = "vllm_") -> str:
    """非同步將上傳檔案寫入臨時檔案並返回路徑"""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(tmp_fd)
    
    try:
        async with aiofiles.open(tmp_path, "wb") as f:
            # 分塊讀寫，避免大檔案佔用過多記憶體
            while chunk := await upload_file.read(8192):  # 8KB chunks
                await f.write(chunk)
        return tmp_path
    except Exception:
        # 如果寫入失敗，清理臨時檔案
        safe_remove_temp_file(tmp_path)
        raise


@app.on_event("shutdown")
async def _shutdown_gateway_client() -> None:
    await gateway_http_client.aclose()

# CORS 設定 (開發時允許所有來源)
# 注意：allow_origins=["*"] 與 allow_credentials=True 在瀏覽器規範中是無效組合
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = """<Role>
你是一位具備頂尖解題能力、同時富有溫度的資深 AI 顧問。無論使用者提出日常閒聊、技術問題或是文本分析，你都能迅速切換心智模式，提供最符合情境的高品質解答。
</Role>

<Constraints>
1. **防截斷與精煉原則**：你的首要防線是「完整表達」，永遠確保在 2000 字以內自然結束話題。若問題龐大，請給出【核心結論】後，詢問使用者是否需展開細節。
2. **結構化呈現**：大量使用 Markdown 語法（粗體、區塊引用、列表）來強化層次。拒絕長篇無排版的文字牆結構。
3. **無廢話開場**：切入正題，不需要「你好，我是 AI 助手」之類的無意義破冰語。
4. **誠實與精確**：面對不懂的問題或缺乏工具連線時，不瞎編、不猜測，精確告知你的能力邊界，不要過度思考鬼打牆
</Constraints>

<Thinking_Process_Guidelines>
- **禁止默寫規則**：絕對不要在思考過程中複誦或列出 Constraint Checklist（限制檢查表）。遇到限制或原則，請在心裡執行，不要寫出來。
- **簡明扼要**：思考過程應專注於問題拆解、邏輯推演與計算。遇到一般閒聊或簡單問題時，請將思考過程縮減至 50 字以內，甚至一語帶過。
- **保留額度**：重要的是你的主要任務是給出最終解答，請將大部分的 token 額度留給輸出給使用者的實際內容。
</Thinking_Process_Guidelines>

<Response_Strategy>
- **遭遇一般提問**：直接給答案，若有選項請列點。
- **遭遇程式/技術問題**：先給【結論與根因】，接著才提供【解決代碼與建議】。
- **遭遇長文本/文件分析**：以【摘要】開頭，再進行【重點條列提取】。
- **遭遇閒聊**：展現高 EQ 與幽默感，引導正面對話。
- **用字遣詞**：使用繁體中文，不要使用簡體中文和emoji表情。
</Response_Strategy>
"""

# ============================================================
# Pydantic 模型
# ============================================================

class ChatRequest(BaseModel):
    """聊天請求 - 預設值從 settings 讀取"""
    message: str
    model: str | None = None
    max_tokens: int = settings.default_max_tokens
    temperature: float = settings.default_temperature
    top_p: float = settings.default_top_p
    top_k: int = settings.default_top_k
    min_p: float = settings.default_min_p
    presence_penalty: float = settings.default_presence_penalty
    repetition_penalty: float = settings.default_repetition_penalty


class ChatResponse(BaseModel):
    """聊天回應"""
    response: str


def _openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    code: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
            }
        },
    )


def _resolve_model_route(model: str | None) -> GatewayRoute | None:
    if not model:
        return gateway_routes.get(gateway_default_model)
    return find_route_for_model(model=model, routes=gateway_routes)


async def _proxy_openai_post(path: str, payload: dict) -> Response:
    requested_model = payload.get("model")
    route = _resolve_model_route(requested_model)
    if route is None:
        available = ", ".join(sorted(gateway_routes.keys()))
        detail_help = get_available_models_help(gateway_routes)
        return _openai_error(
            400,
            f"Model '{requested_model}' not found. Available: {available}\n\n{detail_help}",
            code="model_not_found",
        )

    upstream_payload = dict(payload)
    upstream_payload["model"] = route.model_name

    headers = {
        "Authorization": f"Bearer {route.api_key}",
        "Content-Type": "application/json",
    }
    upstream_url = f"{route.base_url}{path}"
    stream_mode = bool(upstream_payload.get("stream", False))

    try:
        async with gateway_semaphore:
            if stream_mode:
                req = gateway_http_client.build_request(
                    method="POST",
                    url=upstream_url,
                    json=upstream_payload,
                    headers=headers,
                )
                resp = await gateway_http_client.send(req, stream=True)
                if resp.status_code >= 400:
                    body = await resp.aread()
                    await resp.aclose()
                    return Response(
                        content=body,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                    )

                async def _stream_bytes() -> AsyncGenerator[bytes, None]:
                    try:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                yield chunk
                    finally:
                        await resp.aclose()

                return StreamingResponse(
                    _stream_bytes(),
                    media_type=resp.headers.get("content-type", "text/event-stream"),
                )

            resp = await gateway_http_client.post(
                url=upstream_url,
                json=upstream_payload,
                headers=headers,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
    except httpx.TimeoutException:
        return _openai_error(504, f"Upstream timeout for model '{route.alias}'", code="upstream_timeout")
    except httpx.HTTPError:
        logger.exception("Gateway upstream error")
        return _openai_error(503, f"Upstream unavailable for model '{route.alias}'", code="upstream_unavailable")


def _build_text_chat_payload(request: ChatRequest, stream: bool) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": request.message},
    ]
    payload: dict = {
        "model": request.model or gateway_default_model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "presence_penalty": request.presence_penalty,
        "stream": stream,
        "extra_body": {
            "top_k": request.top_k,
            "min_p": request.min_p,
            "repetition_penalty": request.repetition_penalty,
        },
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


# ============================================================
# API 端點
# ============================================================

@app.get("/health")
async def health() -> dict:
    """Gateway 健康檢查。"""
    return {
        "status": "ok",
        "routes": sorted(gateway_routes.keys()),
        "default_model": gateway_default_model,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    """
    就緒檢查 - 檢查所有後端模型是否正常運行
    用於 Kubernetes readiness probe 或負載均衡器健康檢查
    """
    unhealthy_count = 0
    
    for alias, route in gateway_routes.items():
        health_url = f"{route.base_url.rsplit('/v1', 1)[0]}/health"
        try:
            # 嘗試連接到每個模型的健康檢查端點
            # vLLM 通常在 /health 或 /v1/models 端點回應
            resp = await gateway_http_client.get(health_url, timeout=2.0)
            
            if resp.status_code != 200:
                logger.warning("模型健康檢查回傳非 200: %s (%s)", alias, resp.status_code)
                unhealthy_count += 1
        except httpx.TimeoutException:
            logger.warning("模型健康檢查逾時: %s", alias)
            unhealthy_count += 1
        except Exception:
            logger.exception("模型健康檢查失敗: %s", alias)
            unhealthy_count += 1
    
    if unhealthy_count:
        return JSONResponse(
            status_code=503,
            content={
                "ready": False,
                "healthy_count": len(gateway_routes) - unhealthy_count,
                "unhealthy_count": unhealthy_count,
                "total_count": len(gateway_routes)
            }
        )
    
    return JSONResponse(
        status_code=200,
        content={
            "ready": True,
            "healthy_count": len(gateway_routes),
            "total_count": len(gateway_routes)
        }
    )


@app.get("/v1/models")
async def openai_list_models() -> dict:
    """OpenAI Compatible: 列出可用模型 alias（帶快取）。"""
    global _models_cache, _models_cache_time
    
    current_time = time.time()
    
    # 檢查快取是否有效
    if _models_cache is not None and (current_time - _models_cache_time) < _MODELS_CACHE_TTL:
        return _models_cache
    
    # 重新建立模型列表
    data = [
        {
            "id": route.alias,
            "object": "model",
            "owned_by": "vllm",
        }
        for route in gateway_routes.values()
    ]
    result = {"object": "list", "data": data}
    
    # 更新快取
    _models_cache = result
    _models_cache_time = current_time
    
    return result


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request) -> Response:
    """OpenAI Compatible: 多模型聊天代理。"""
    try:
        payload = await request.json()
    except Exception:
        return _openai_error(400, "Invalid JSON payload", code="bad_request")
    if not isinstance(payload, dict):
        return _openai_error(400, "JSON payload must be an object", code="bad_request")
    return await _proxy_openai_post("/chat/completions", payload)


@app.post("/v1/completions")
async def openai_completions(request: Request) -> Response:
    """OpenAI Compatible: 多模型 completion 代理。"""
    try:
        payload = await request.json()
    except Exception:
        return _openai_error(400, "Invalid JSON payload", code="bad_request")
    if not isinstance(payload, dict):
        return _openai_error(400, "JSON payload must be an object", code="bad_request")
    return await _proxy_openai_post("/completions", payload)


@app.get("/")
async def root():
    """根路徑"""
    return {
        "service": "vLLM API Gateway",
        "model": client.model_name,
        "is_vision_model": client.is_vision_model,
        "status": "running"
    }


@app.get("/api/model-info")
async def model_info():
    """獲取模型資訊"""
    return {
        "model_name": client.model_name,
        "is_vision_model": client.is_vision_model,
        "is_image_capable": client.is_image_capable,
        "api_base": f"http://{gateway_host}:{gateway_port}",
        "default_model": gateway_default_model,
        "available_models": sorted(gateway_routes.keys()),
    }


@app.get("/api/config")
async def get_config():
    """獲取推論配置。"""
    return {
        "default_max_tokens": settings.default_max_tokens,
        "default_temperature": settings.default_temperature,
        "document_max_tokens": settings.document_max_tokens,
        "vision_temperature": settings.vision_temperature,
        "default_top_p": settings.default_top_p,
        "default_top_k": settings.default_top_k,
        "default_min_p": settings.default_min_p,
        "default_presence_penalty": settings.default_presence_penalty,
        "default_repetition_penalty": settings.default_repetition_penalty,
        "video_fps": settings.video_fps,
        "video_chunk_size": settings.max_video_frames_per_chunk,
    }


@app.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    """
    文字聊天 (非流式)
    """
    payload = _build_text_chat_payload(request, stream=False)
    route = _resolve_model_route(payload.get("model"))
    if route is None:
        available = ", ".join(sorted(gateway_routes.keys()))
        detail_help = get_available_models_help(gateway_routes)
        raise HTTPException(
            status_code=400,
            detail=f"Model '{payload.get('model')}' not found. Available: {available}\n\n{detail_help}",
        )

    upstream_payload = dict(payload)
    upstream_payload["model"] = route.model_name
    try:
        resp = await gateway_http_client.post(
            url=f"{route.base_url}/chat/completions",
            json=upstream_payload,
            headers={
                "Authorization": f"Bearer {route.api_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ChatResponse(response=content or "")
    except HTTPException:
        raise
    except Exception:
        logger.exception("文字聊天處理失敗")
        raise HTTPException(status_code=500, detail="處理請求時發生內部錯誤")


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    文字聊天 (流式)
    Server-Sent Events (SSE) 格式
    """
    payload = _build_text_chat_payload(request, stream=True)
    return await _proxy_openai_post("/chat/completions", payload)


@app.post("/api/chat/vision")
async def chat_vision(
    message: str = Form(...),
    image: UploadFile = File(...),
    max_tokens: int = Form(settings.default_max_tokens),
    temperature: float = Form(settings.vision_temperature),
):
    """
    視覺聊天 (非流式)
    上傳圖片 + 文字提示
    """
    if not client.is_image_capable:
        raise HTTPException(
            status_code=400,
            detail="當前模型不支援視覺輸入"
        )

    try:
        # 驗證檔案大小
        await validate_file_size(image)
        
        # 驗證副檔名
        validate_file_extension(image.filename, ALLOWED_IMAGE_EXTENSIONS)
        
        # 讀取圖片
        image_bytes = await image.read()
        
        # 驗證圖片內容（magic bytes）
        await validate_image_content(image_bytes)
        
        # 轉換為 Base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        # 構建多模態內容
        from utils.image_utils import create_multimodal_content_from_base64
        
        content = create_multimodal_content_from_base64(
            text=message,
            image_base64=image_b64,
            mime_type=image.content_type or "image/jpeg"
        )
        
        # 呼叫模型
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}
        ]
        response = await client.achat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )
        
        return ChatResponse(response=response.choices[0].message.content or "")
    
    except HTTPException:
        raise
    except Exception:
        logger.exception("視覺聊天處理失敗")
        raise HTTPException(status_code=500, detail="處理請求時發生內部錯誤")


@app.post("/api/chat/vision/stream")
async def chat_vision_stream(
    message: str = Form(...),
    image: UploadFile = File(...),
    max_tokens: int = Form(settings.default_max_tokens),
    temperature: float = Form(settings.vision_temperature),
):
    """
    視覺聊天 (流式)
    上傳圖片 + 文字提示，流式返回
    """
    if not client.is_image_capable:
        raise HTTPException(
            status_code=400,
            detail="當前模型不支援視覺輸入"
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 驗證檔案大小
            await validate_file_size(image)
            
            # 驗證副檔名
            validate_file_extension(image.filename, ALLOWED_IMAGE_EXTENSIONS)
            
            # 讀取圖片
            image_bytes = await image.read()
            
            # 驗證圖片內容
            await validate_image_content(image_bytes)
            
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            # 構建多模態內容
            from utils.image_utils import create_multimodal_content_from_base64
            
            content = create_multimodal_content_from_base64(
                text=message,
                image_base64=image_b64,
                mime_type=image.content_type or "image/jpeg"
            )
            
            # 呼叫模型
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content}
            ]
            stream = await client.achat(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
            
            start_time = time.time()
            
            # 流式輸出
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    yield f"data: {json.dumps(delta)}\n\n"
                
                if hasattr(chunk, 'usage') and chunk.usage:
                    elapsed = time.time() - start_time
                    tokens = chunk.usage.completion_tokens
                    tps = tokens / elapsed if elapsed > 0 else 0
                    stats = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": tokens,
                        "total_tokens": chunk.usage.total_tokens,
                        "tps": round(tps, 1),
                        "time": round(elapsed, 2)
                    }
                    yield f"data: [STATS] {json.dumps(stats)}\n\n"
            
            yield "data: [DONE]\n\n"
        
        except Exception:
            logger.exception("視覺聊天流式處理失敗")
            yield 'data: [ERROR] 處理請求時發生內部錯誤\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/chat/document/stream")
async def chat_document_stream(
    message: str = Form(...),
    document: UploadFile = File(...),
    max_tokens: int = Form(settings.document_max_tokens),
    temperature: float = Form(settings.default_temperature),
):
    """
    文件聊天 (流式)
    上傳文件 (DOCX/PDF/TXT) + 文字提示，流式返回
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 驗證檔案大小
            await validate_file_size(document)
            
            # 驗證副檔名
            validate_file_extension(document.filename, ALLOWED_DOCUMENT_EXTENSIONS)
            
            # 讀取文件
            document_bytes = await document.read()
            
            # 提取文件內容
            from utils.document_utils import extract_document, create_document_prompt
            
            result = extract_document(document_bytes, filename=document.filename or "document.txt")
            
            if not result['success']:
                logger.error("文件提取失敗: %s", result['error'])
                yield 'data: [ERROR] 文件解析失敗，請確認文件格式正確\n\n'
                return
            
            # 構建 System Prompt（策略 C：System + User 角色分離）
            system_prompt = """<Role>
你是一位具備頂尖文本分析與邏輯推理能力的資深 AI 文件顧問。你的任務是精準理解使用者提供的文件內容，並提供最符合情境的高品質解析與問答。
</Role>

<Constraints>
1. **忠於原文**：所有回答必須嚴格基於提供的文件內容。若遇到文件中未提及的資訊，請誠實精確地告知「文件中沒有提供相關資訊」，絕不憑空捏造或添加外部假設。
2. **結構化呈現**：大量使用 Markdown 語法（粗體、區塊引用、列表、標題）來強化層次。拒絕長篇無排版的文字牆結構，複雜內容應分段或條列說明。
3. **無廢話開場**：切入正題，不需要「你好，我是 AI 助手」或「根據文件內容」之類的無意義破冰語。
4. **精確歸納**：在闡述觀點或提供事實時，能良好統整文件中的情境、段落或重要依據來支撐你的回答。
</Constraints>

<Thinking_Process_Guidelines>
- **禁止默寫規則**：絕對不要在思考過程中複誦或列出 Constraint Checklist（限制檢查表）。
- **深度推理**：思考過程應專注於文件內容的交叉比對、邏輯梳理與資訊萃取。確保最終回答的邏輯嚴密且切中要害。
- **保留額度**：確保將大部分的 token 額度留給最終輸出的實際內容，而非一再重述已知事實。
</Thinking_Process_Guidelines>

<Response_Strategy>
- **遭遇全文總結/綱要提問**：以【重點摘要】開頭，再進行【細節條列提取】。
- **遭遇特定細節提問**：立刻給出精確答案，並附上相關的文件脈絡。
- **遇到文件矛盾或語意不清**：主動點出文件中的矛盾處或模糊地帶，並客觀呈現差異。
- **用字遣詞**：使用繁體中文，維持專業且客觀的語氣，不要使用簡體中文和 emoji 表情。
</Response_Strategy>"""

            # 構建用戶消息（包含文件內容和問題）
            user_content = create_document_prompt(
                document_content=result['content'],
                user_message=message,
                file_type=result['file_type']
            )
            
            # 構建消息列表
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
            
            # 呼叫模型（流式）
            stream = await client.achat(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
            
            start_time = time.time()
            
            # 流式輸出
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    yield f"data: {json.dumps(delta)}\n\n"
                    
                if hasattr(chunk, 'usage') and chunk.usage:
                    elapsed = time.time() - start_time
                    tokens = chunk.usage.completion_tokens
                    tps = tokens / elapsed if elapsed > 0 else 0
                    stats = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": tokens,
                        "total_tokens": chunk.usage.total_tokens,
                        "tps": round(tps, 1),
                        "time": round(elapsed, 2)
                    }
                    yield f"data: [STATS] {json.dumps(stats)}\n\n"
            
            yield "data: [DONE]\n\n"
        
        except Exception:
            logger.exception("文件處理失敗")
            yield 'data: [ERROR] 處理請求時發生內部錯誤\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )



@app.post("/api/chat/video/info")
async def chat_video_info(
    video: UploadFile = File(...),
):
    """
    影片預檢 - 回傳影片元資料與預估分段數
    呼叫端可在上傳影片後取得預檢資訊。
    """
    if not client.is_image_capable:
        raise HTTPException(status_code=400, detail="當前模型不支援視覺輸入")

    tmp_path = None
    try:
        # 驗證檔案大小
        await validate_file_size(video)
        
        # 驗證副檔名
        suffix = validate_file_extension(video.filename, ALLOWED_VIDEO_EXTENSIONS)
        
        # 使用非同步 I/O 寫入臨時檔案
        tmp_path = await write_upload_to_temp_async(video, suffix=suffix, prefix="vllm_info_")

        from utils.video_utils import get_video_info, plan_chunks

        info = get_video_info(tmp_path)
        sample_frames = max(1, int(info.duration_sec * settings.video_fps))
        chunk_plan = plan_chunks(sample_frames, settings.max_video_frames_per_chunk)

        return {
            "duration": round(info.duration_sec, 1),
            "width": info.width,
            "height": info.height,
            "native_fps": round(info.native_fps, 2),
            "total_frames": info.total_frames,
            "sample_frames": sample_frames,
            "num_chunks": chunk_plan.num_chunks,
            "chunk_size": chunk_plan.chunk_size,
            "use_chunked": chunk_plan.use_chunked,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("影片解析失敗")
        raise HTTPException(status_code=422, detail="影片解析失敗")
    finally:
        safe_remove_temp_file(tmp_path)


@app.post("/api/chat/video/stream")
async def chat_video_stream(
    message: str = Form(...),
    video: UploadFile = File(...),
    max_tokens: int = Form(settings.default_max_tokens),
    temperature: float = Form(settings.vision_temperature),
):
    """
    影片聊天 (流式)
    上傳影片 + 文字提示，流式返回 SSE
    自動兼容單段與多段分部推論
    """
    if not client.is_image_capable:
        raise HTTPException(status_code=400, detail="當前模型不支援視覺輸入")

    async def event_generator() -> AsyncGenerator[str, None]:
        tmp_path = None
        try:
            # 驗證檔案大小
            await validate_file_size(video)
            
            # 驗證副檔名
            suffix = validate_file_extension(video.filename, ALLOWED_VIDEO_EXTENSIONS)
            
            # 使用非同步 I/O 寫入臨時檔案
            tmp_path = await write_upload_to_temp_async(video, suffix=suffix, prefix="vllm_video_")

            # 影片預檢資訊
            from utils.video_utils import get_video_info, plan_chunks

            try:
                info = get_video_info(tmp_path)
                sample_frames = max(1, int(info.duration_sec * settings.video_fps))
                chunk_plan = plan_chunks(sample_frames, settings.max_video_frames_per_chunk)
                info_payload = {
                    "duration": round(info.duration_sec, 1),
                    "frames": sample_frames,
                    "chunks": chunk_plan.num_chunks,
                }
                yield f"data: [INFO] {json.dumps(info_payload)}\n\n"
            except Exception:
                pass  # 即使預檢失敗，仍繼續推論

            # 組合 Message
            # 因為 api/client.py 的 chat_with_video_stream 預期 text 參數直接是單純的字串 prompt，
            # 若要傳遞 system prompt 給 client 的 achat_with_video_stream 比較困難，
            # 我們可以直接將 system prompt 和 user prompt 結合成一段文字傳遞給 text 參數。
            combined_message = f"{SYSTEM_PROMPT}\n\n用戶要求： {message}"

            start_time = time.time()
            chunk_count = 0

            # 流式推論（單段直接流式 / 多段分部後流式彙整段）
            async for token in client.achat_with_video_stream(
                text=combined_message,
                video_path=tmp_path,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                chunk_count += 1
                yield f"data: {json.dumps(token)}\n\n"

            elapsed = time.time() - start_time
            tps = chunk_count / elapsed if elapsed > 0 else 0
            stats = {
                "completion_tokens": chunk_count, # Estimated tokens for video stream
                "tps": round(tps, 1),
                "time": round(elapsed, 2)
            }
            yield f"data: [STATS] {json.dumps(stats)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception:
            logger.exception("影片處理失敗")
            yield 'data: [ERROR] 處理請求時發生內部錯誤\n\n'
        finally:
            safe_remove_temp_file(tmp_path)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "gateway.main:app",
        host=gateway_host,
        port=gateway_port,
        reload=True,
    )
