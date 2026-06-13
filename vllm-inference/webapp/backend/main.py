"""
FastAPI Web 服務 - 提供 React UI 和 API 代理
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 導入專案的 API 客戶端
sys.path.append(str(Path(__file__).parent.parent.parent))

from api.client import ModelClient  # noqa: E402
from config.settings import get_settings  # noqa: E402

logger = logging.getLogger(__name__)

# 初始化
app = FastAPI(title="vLLM Web UI", version="1.0.0")
settings = get_settings()
client = ModelClient(settings)

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


# ============================================================
# API 端點
# ============================================================

@app.get("/")
async def root():
    """根路徑"""
    return {
        "service": "vLLM Web UI",
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
        "api_base": f"http://{settings.api_host}:{settings.api_port}",
    }


@app.get("/api/config")
async def get_config():
    """獲取推論配置 - 給前端使用"""
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
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request.message}
        ]

        response = await client.achat(
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            top_k=request.top_k,
            min_p=request.min_p,
            presence_penalty=request.presence_penalty,
            repetition_penalty=request.repetition_penalty,
            stream=False,
        )
        return ChatResponse(response=response.choices[0].message.content or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    文字聊天 (流式)
    Server-Sent Events (SSE) 格式
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": request.message}
            ]

            stream = await client.achat(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                min_p=request.min_p,
                presence_penalty=request.presence_penalty,
                repetition_penalty=request.repetition_penalty,
                stream=True,
                stream_options={"include_usage": True},
            )
            
            start_time = time.time()
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    # SSE 格式: data: {content}\n\n (JSON Escape for robust newline handling)
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
            
            # 結束標記
            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("聊天流式處理失敗")
            yield 'data: [ERROR] 處理請求時發生內部錯誤\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


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
        # 讀取圖片
        image_bytes = await image.read()
        
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
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
            # 讀取圖片
            image_bytes = await image.read()
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
            # 讀取文件
            document_bytes = await document.read()
            
            # 提取文件內容
            from utils.document_utils import extract_document, create_document_prompt
            
            result = extract_document(document_bytes, filename=document.filename)
            
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
    前端可在上傳影片後即時顯示資訊
    """
    if not client.is_image_capable:
        raise HTTPException(status_code=400, detail="當前模型不支援視覺輸入")

    tmp_path = None
    try:
        video_bytes = await video.read()
        suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="vllm_info_")
        os.close(tmp_fd)
        with open(tmp_path, "wb") as f:
            f.write(video_bytes)

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
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"影片解析失敗: {str(e)}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
            # 儲存上傳影片到 temp 目錄
            video_bytes = await video.read()
            suffix = Path(video.filename or "video.mp4").suffix or ".mp4"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="vllm_video_")
            os.close(tmp_fd)
            with open(tmp_path, "wb") as f:
                f.write(video_bytes)

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
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    # 暫存檔可能已被移除
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# 靜態檔案 (React build)
# ============================================================

# 生產環境：掛載 React build 目錄
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3000,
        reload=True,
    )
