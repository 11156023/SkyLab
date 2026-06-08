"""
API 客戶端層 - 透過 OpenAI SDK 呼叫 vLLM 模型
支持同步與異步呼叫，流式與非流式回應
支持視覺模型的多模態輸入（文字 + 圖片）
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Iterator, List, Union

from openai import AsyncOpenAI, OpenAI

from config.settings import Settings, get_settings

# 視覺模型白名單：名稱包含以下關鍵字就開啟圖片辨識（副效果為自動檢測未涵蓋的模型）
VISION_WHITELIST_KEYWORDS: list[str] = ["qwen3.5"]


class ModelClient:
    """
    模型 API 客戶端
    封裝 OpenAI SDK，用於與 vLLM OpenAI-Compatible Server 互動
    支援視覺模型的多模態輸入
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._base_url = f"http://{self.settings.api_host}:{self.settings.api_port}/v1"
        self._api_key = self.settings.api_key

        # 檢測是否為視覺模型
        self._is_vision_model = self.settings._is_vision_model()

        # 配置連接池參數（改善高併發場景）
        import httpx
        http_client_config = httpx.Limits(
            max_connections=100,            # 最大連接數
            max_keepalive_connections=20,   # 保持活躍連接數
            keepalive_expiry=30.0,          # 連接保持時間
        )

        # 同步客戶端
        self._sync_client = OpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self.settings.request_timeout,
            max_retries=0,  # 禁用自動重試，我們手動控制
        )

        # 異步客戶端
        self._async_client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self.settings.request_timeout,
            max_retries=0,  # 禁用自動重試
            http_client=httpx.AsyncClient(limits=http_client_config),
        )

    @property
    def model_name(self) -> str:
        return self.settings.resolved_model_path

    @property
    def is_vision_model(self) -> bool:
        """判斷當前模型是否為視覺模型"""
        return self._is_vision_model

    @property
    def is_image_capable(self) -> bool:
        """是否支援圖片輸入（視覺模型 或 白名單模型）"""
        if self._is_vision_model:
            return True
        model_lower = self.model_name.lower()
        return any(kw in model_lower for kw in VISION_WHITELIST_KEYWORDS)

    # ============================================================
    # 同步方法
    # ============================================================

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        presence_penalty: float | None = None,
        repetition_penalty: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        同步 Chat Completion

        Args:
            messages: 對話訊息列表 [{"role": "user", "content": "..."}]
            max_tokens: 最大生成 token 數 (None 時使用 settings 預設值)
            temperature: 溫度參數 (None 時使用 settings 預設值)
            top_p: Top-P 取樣 (None 時使用 settings 預設值)
            top_k: Top-K 取樣，vLLM 擴展參數 (None 時使用 settings 預設值)
            min_p: Min-P 取樣，vLLM 擴展參數 (None 時使用 settings 預設值)
            presence_penalty: 存在懲罰 (None 時使用 settings 預設值)
            repetition_penalty: 重複懲罰，vLLM 擴展參數 (None 時使用 settings 預設值)
            stream: 是否流式回應
            **kwargs: 額外參數 (傳入 extra_body)

        Returns:
            ChatCompletion 或流式迭代器
        """
        # 使用 settings 預設值（如果未指定）
        if max_tokens is None:
            max_tokens = self.settings.default_max_tokens
        if temperature is None:
            temperature = self.settings.default_temperature
        if top_p is None:
            top_p = self.settings.default_top_p
        if top_k is None:
            top_k = self.settings.default_top_k
        if min_p is None:
            min_p = self.settings.default_min_p
        if presence_penalty is None:
            presence_penalty = self.settings.default_presence_penalty
        if repetition_penalty is None:
            repetition_penalty = self.settings.default_repetition_penalty

        # stream_options 必須先 pop，再 update extra_body，避免間接入 extra_body
        stream_options = kwargs.pop("stream_options", None)

        # vLLM 擴展參數透過 extra_body 傳遞
        extra_body: dict = {"top_k": top_k, "min_p": min_p, "repetition_penalty": repetition_penalty}
        extra_body.update(kwargs)

        response = self._sync_client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            presence_penalty=presence_penalty,
            stream=stream,
            stream_options=stream_options,
            extra_body=extra_body,
        )
        return response

    def chat_simple(self, prompt: str, **kwargs) -> str:
        """
        簡化版同步對話 - 傳入字串，回傳字串

        Args:
            prompt: 使用者提示
            **kwargs: 傳遞給 chat() 的額外參數

        Returns:
            模型回應文字
        """
        messages = [{"role": "user", "content": prompt}]
        response = self.chat(messages, stream=False, **kwargs)
        return response.choices[0].message.content or ""

    def chat_stream(self, prompt: str, **kwargs) -> Iterator[str]:
        """
        同步流式對話

        Args:
            prompt: 使用者提示
            **kwargs: 額外參數

        Yields:
            逐步生成的文字片段
        """
        messages = [{"role": "user", "content": prompt}]
        stream = self.chat(messages, stream=True, **kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def complete(
        self,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs,
    ):
        """
        同步 Text Completion

        Args:
            prompt: 文字提示
            max_tokens: 最大 token 數 (None 時使用 settings 預設值)
            temperature: 溫度 (None 時使用 settings 預設值)
            **kwargs: 額外參數

        Returns:
            Completion 回應
        """
        if max_tokens is None:
            max_tokens = self.settings.default_max_tokens
        if temperature is None:
            temperature = self.settings.default_temperature
            
        response = self._sync_client.completions.create(
            model=self.model_name,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=kwargs if kwargs else None,
        )
        return response

    # ============================================================
    # 視覺模型方法 (Vision-Language Models)
    # ============================================================

    def chat_with_image(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        視覺模型對話 - 支援圖片輸入
        
        如果當前模型不是視覺模型，會自動忽略圖片，只處理文字

        Args:
            text: 文字提示
            image_paths: 圖片路徑（單個或列表）
            max_tokens: 最大生成 token 數 (None 時使用 settings 預設值)
            temperature: 溫度參數 (None 時使用視覺模型預設溫度)
            stream: 是否流式回應
            **kwargs: 額外參數

        Returns:
            ChatCompletion 或流式迭代器

        Examples:
            >>> client = ModelClient()
            >>> # 單張圖片
            >>> response = client.chat_with_image("描述這張圖片", "image.jpg")
            >>> print(response)
            >>> 
            >>> # 多張圖片
            >>> response = client.chat_with_image(
            ...     "比較這些圖片",
            ...     ["image1.jpg", "image2.jpg"]
            ... )
        """
        # 使用 settings 預設值
        if max_tokens is None:
            max_tokens = self.settings.default_max_tokens
        if temperature is None:
            temperature = self.settings.vision_temperature if self._is_vision_model else self.settings.default_temperature
        
        # 檢查是否為視覺模型
        if not self._is_vision_model:
            print("[Warning] 當前模型不支援視覺輸入，忽略圖片，僅處理文字")
            return self.chat_simple(text, max_tokens=max_tokens, temperature=temperature, **kwargs)
        
        # 處理圖片路徑（轉為列表）
        if isinstance(image_paths, (str, Path)):
            image_paths = [image_paths]
        
        # 導入圖片處理工具
        try:
            from utils.image_utils import create_multimodal_content
        except ImportError:
            raise ImportError(
                "需要 utils.image_utils 模組來處理圖片\n"
                "請確保 utils/ 目錄存在且包含 image_utils.py"
            )
        
        
        # 創建多模態內容
        content = create_multimodal_content(
            text=text,
            image_paths=image_paths,
            resize=self.settings.enable_image_resize,
            max_size=self.settings.max_image_size,
        )
        
        # 構建消息
        messages = [{"role": "user", "content": content}]
        
        # 呼叫 chat
        return self.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            **kwargs,
        )

    def chat_with_image_simple(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        **kwargs,
    ) -> str:
        """
        簡化版視覺對話 - 返回字串
        
        Args:
            text: 文字提示
            image_paths: 圖片路徑（單個或列表）
            **kwargs: 額外參數
            
        Returns:
            模型回應文字
            
        Examples:
            >>> client = ModelClient()
            >>> answer = client.chat_with_image_simple("這是什麼？", "photo.jpg")
            >>> print(answer)
        """
        response = self.chat_with_image(
            text=text,
            image_paths=image_paths,
            stream=False,
            **kwargs,
        )
        
        # 如果不是視覺模型，直接返回字串
        if isinstance(response, str):
            return response
        
        return response.choices[0].message.content or ""

    def chat_with_image_stream(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        **kwargs,
    ) -> Iterator[str]:
        """
        視覺對話流式輸出
        
        Args:
            text: 文字提示
            image_paths: 圖片路徑（單個或列表）
            **kwargs: 額外參數
            
        Yields:
            逐步生成的文字片段
        """
        response = self.chat_with_image(
            text=text,
            image_paths=image_paths,
            stream=True,
            **kwargs,
        )
        
        # 如果不是視覺模型，yield 字串
        if isinstance(response, str):
            yield response
            return
        
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ============================================================
    # 影片模型方法 (Video)
    # ============================================================

    def _load_video_chunks(self, video_path, fps, chunk_size, frame_size, quality):
        """
        內部輔助：載入影片幀、計劃分段。
        回傳 (chunks, video_info, plan)。
        """
        try:
            from utils.video_utils import prepare_video_chunks
        except ImportError:
            raise ImportError(
                "需要 utils.video_utils 模組\n"
                "請確保安裝: pip install opencv-python-headless"
            )
        return prepare_video_chunks(
            video_path,
            fps=fps,
            chunk_size=chunk_size,
            max_size=frame_size,
            quality=quality,
        )

    def _build_chunk_prompt(self, chunk, user_question: str) -> str:
        """建立單段推論 prompt（分段模式用）。"""
        template = self.settings.video_chunk_prompt
        return template.format(
            chunk_index=chunk.chunk_index,
            total_chunks=chunk.total_chunks,
            start_sec=chunk.start_sec,
            end_sec=chunk.end_sec,
            num_frames=len(chunk.frames_b64),
            user_question=user_question,
        )

    def _build_merge_prompt(self, summaries: list[str], user_question: str, total_chunks: int) -> str:
        """建立彙整 prompt（多段彩用）。"""
        numbered = "\n\n".join(
            f"第 {i+1} 段：\n{s}" for i, s in enumerate(summaries)
        )
        template = self.settings.video_merge_prompt
        return template.format(
            total_chunks=total_chunks,
            summaries=numbered,
            user_question=user_question,
        )

    def chat_with_video(
        self,
        text: str,
        video_path: Union[str, Path],
        fps: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        影片對話 (同步)。

        幀數超過 max_video_frames_per_chunk 時自動切分多段推論。

        Args:
            text:        使用者提問
            video_path:  影片檔案路徑
            fps:         抽幀速率（None 則使用 settings.video_fps）
            max_tokens:  最大生成 token
            temperature: 溫度
            stream:      是否流式回應（只適用於單段或最後彙整段）

        Returns:
            單段：ChatCompletion 或流式迭代器
            多段：彙整後的完整字串
        """
        from utils.video_utils import build_video_message, write_frames_to_video

        _fps       = fps         if fps         is not None else self.settings.video_fps
        _max_tok   = max_tokens  if max_tokens  is not None else self.settings.default_max_tokens
        _temp      = temperature if temperature is not None else self.settings.vision_temperature
        chunk_size = self.settings.max_video_frames_per_chunk
        frame_size = self.settings.max_video_frame_size
        quality    = self.settings.video_frame_quality

        chunks, info, plan = self._load_video_chunks(
            video_path, _fps, chunk_size, frame_size, quality
        )

        total = plan.total_sampled_frames
        print(f"[Video] 影片資訊: {info.duration_sec:.1f}s  "
              f"抽樣幀數={total}  "
              f"分段={plan.num_chunks}  "
              f"(分段上限={chunk_size} 幀/段)")

        # === 單段：直接將原始影片路徑傳出（file:// URL）===
        if plan.num_chunks == 1:
            msg = build_video_message(text, video_path)
            return self.chat(
                messages=[msg],
                max_tokens=_max_tok,
                temperature=_temp,
                stream=stream,
                **kwargs,
            )

        # === 多段：每段寫 temp MP4 再推論，最後彙整 ===
        import tempfile
        import os
        temp_files: list[str] = []
        summaries: list[str] = []
        try:
            for chunk in chunks:
                # 寫入 temp 影片檔
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="vllm_chunk_")
                os.close(tmp_fd)
                temp_files.append(tmp_path)
                write_frames_to_video(chunk.frames_b64, tmp_path, fps=_fps)

                chunk_prompt = self._build_chunk_prompt(chunk, text)
                print(f"[Video] 推論第 {chunk.chunk_index}/{plan.num_chunks} 段"
                      f" ({len(chunk.frames_b64)} 幀, "
                      f"{chunk.start_sec:.1f}s~{chunk.end_sec:.1f}s)...")
                msg = build_video_message(chunk_prompt, tmp_path)
                resp = self.chat(
                    messages=[msg],
                    max_tokens=_max_tok,
                    temperature=_temp,
                    stream=False,
                    **kwargs,
                )
                summary = resp.choices[0].message.content or ""
                summaries.append(summary)
                print(f"[Video] 第 {chunk.chunk_index} 段完成，摘要 {len(summary)} 字")
        finally:
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    # Ignore errors when deleting temporary video chunks
                    pass

        # 彙整段
        merge_prompt = self._build_merge_prompt(summaries, text, plan.num_chunks)
        print(f"[Video] 彙整 {plan.num_chunks} 段結果...")
        return self.chat(
            messages=[{"role": "user", "content": merge_prompt}],
            max_tokens=_max_tok,
            temperature=_temp,
            stream=stream,
            **kwargs,
        )

    def chat_with_video_simple(
        self,
        text: str,
        video_path: Union[str, Path],
        **kwargs,
    ) -> str:
        """
        影片對話（簡化版，回傳字串）。

        Args:
            text:       使用者提問
            video_path: 影片路徑
            **kwargs:   傳送給 chat_with_video() 的額外參數

        Returns:
            模型回應字串
        """
        response = self.chat_with_video(
            text=text,
            video_path=video_path,
            stream=False,
            **kwargs,
        )
        # 多段模式回傳的已是字串（彙整段）
        if isinstance(response, str):
            return response
        return response.choices[0].message.content or ""

    def chat_with_video_stream(
        self,
        text: str,
        video_path: Union[str, Path],
        **kwargs,
    ) -> Iterator[str]:
        """
        影片對話流式輸出。

        單段模式：流式輸出最終答案。
        多段模式：非流式完成各段摘要，流式輸出彙整段。

        Yields:
            逐步生成的文字片段
        """
        response = self.chat_with_video(
            text=text,
            video_path=video_path,
            stream=True,
            **kwargs,
        )
        # 多段模式回傳字串（已彙整）
        if isinstance(response, str):
            yield response
            return
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ============================================================
    # 異步方法
    # ============================================================

    async def achat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        presence_penalty: float | None = None,
        repetition_penalty: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        異步 Chat Completion

        Args:
            messages: 對話訊息列表
            max_tokens: 最大 token 數 (None 時使用 settings 預設值)
            temperature: 溫度參數 (None 時使用 settings 預設值)
            top_p: Top-P 取樣 (None 時使用 settings 預設值)
            top_k: Top-K 取樣，vLLM 擴展參數 (None 時使用 settings 預設值)
            min_p: Min-P 取樣，vLLM 擴展參數 (None 時使用 settings 預設值)
            presence_penalty: 存在懲罰 (None 時使用 settings 預設值)
            repetition_penalty: 重複懲罰，vLLM 擴展參數 (None 時使用 settings 預設值)
            stream: 是否流式
            **kwargs: 額外參數

        Returns:
            ChatCompletion 或異步流式迭代器
        """
        # 使用 settings 預設值
        if max_tokens is None:
            max_tokens = self.settings.default_max_tokens
        if temperature is None:
            temperature = self.settings.default_temperature
        if top_p is None:
            top_p = self.settings.default_top_p
        if top_k is None:
            top_k = self.settings.default_top_k
        if min_p is None:
            min_p = self.settings.default_min_p
        if presence_penalty is None:
            presence_penalty = self.settings.default_presence_penalty
        if repetition_penalty is None:
            repetition_penalty = self.settings.default_repetition_penalty

        # stream_options 必須先 pop，再 update extra_body，避免間接入 extra_body
        stream_options = kwargs.pop("stream_options", None)

        # vLLM 擴展參數透過 extra_body 傳遞
        extra_body: dict = {"top_k": top_k, "min_p": min_p, "repetition_penalty": repetition_penalty}
        extra_body.update(kwargs)

        response = await self._async_client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            presence_penalty=presence_penalty,
            stream=stream,
            stream_options=stream_options,
            extra_body=extra_body,
        )
        return response

    async def achat_simple(self, prompt: str, **kwargs) -> str:
        """
        簡化版異步對話

        Args:
            prompt: 使用者提示

        Returns:
            模型回應文字
        """
        messages = [{"role": "user", "content": prompt}]
        response = await self.achat(messages, stream=False, **kwargs)
        return response.choices[0].message.content or ""

    async def achat_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """
        異步流式對話

        Yields:
            逐步生成的文字片段
        """
        messages = [{"role": "user", "content": prompt}]
        stream = await self.achat(messages, stream=True, **kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ============================================================
    # 異步視覺模型方法
    # ============================================================

    async def achat_with_image(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        異步視覺模型對話 - 支援圖片輸入

        Args:
            text: 文字提示
            image_paths: 圖片路徑（單個或列表）
            max_tokens: 最大生成 token 數 (None 時使用 settings 預設值)
            temperature: 溫度參數 (None 時使用視覺模型預設溫度)
            stream: 是否流式回應
            **kwargs: 額外參數

        Returns:
            ChatCompletion 或異步流式迭代器
        """
        # 使用 settings 預設值
        if max_tokens is None:
            max_tokens = self.settings.default_max_tokens
        if temperature is None:
            temperature = self.settings.vision_temperature if self._is_vision_model else self.settings.default_temperature
        
        # 檢查是否為視覺模型
        if not self._is_vision_model:
            print("[Warning] 當前模型不支援視覺輸入，忽略圖片，僅處理文字")
            return await self.achat_simple(text, max_tokens=max_tokens, temperature=temperature, **kwargs)
        
        # 處理圖片路徑
        if isinstance(image_paths, (str, Path)):
            image_paths = [image_paths]
        
        # 導入圖片處理工具
        try:
            from utils.image_utils import create_multimodal_content
        except ImportError:
            raise ImportError(
                "需要 utils.image_utils 模組來處理圖片\n"
                "請確保 utils/ 目錄存在且包含 image_utils.py"
            )
        
        
        # 創建多模態內容
        content = create_multimodal_content(
            text=text,
            image_paths=image_paths,
            resize=self.settings.enable_image_resize,
            max_size=self.settings.max_image_size,
        )
        
        # 構建消息
        messages = [{"role": "user", "content": content}]
        
        # 呼叫 achat
        return await self.achat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            **kwargs,
        )

    async def achat_with_image_simple(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        **kwargs,
    ) -> str:
        """
        異步簡化版視覺對話
        
        Args:
            text: 文字提示
            image_paths: 圖片路徑（單個或列表）
            **kwargs: 額外參數
            
        Returns:
            模型回應文字
        """
        response = await self.achat_with_image(
            text=text,
            image_paths=image_paths,
            stream=False,
            **kwargs,
        )
        
        if isinstance(response, str):
            return response
        
        return response.choices[0].message.content or ""

    async def achat_with_image_stream(
        self,
        text: str,
        image_paths: Union[str, Path, List[Union[str, Path]]],
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        異步視覺對話流式輸出
        
        Args:
            text: 文字提示
            image_paths: 圖片路徑
            **kwargs: 額外參數
            
        Yields:
            逐步生成的文字片段
        """
        response = await self.achat_with_image(
            text=text,
            image_paths=image_paths,
            stream=True,
            **kwargs,
        )
        
        if isinstance(response, str):
            yield response
            return
        
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ============================================================
    # 影片模型異步方法 (Video Async)
    # ============================================================

    async def achat_with_video(
        self,
        text: str,
        video_path: Union[str, Path],
        fps: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        影片對話 (異步)。

        幀數超過 max_video_frames_per_chunk 時自動切分多段推論。
        中間段等待鈦行製行，現在版本為序列異步。

        Args:
            text:        使用者提問
            video_path:  影片檔案路徑
            fps:         抽幀速率（None 則使用 settings.video_fps）
            max_tokens:  最大生成 token
            temperature: 溫度
            stream:      是否流式回應

        Returns:
            單段：ChatCompletion 或異步流式迭代器
            多段：彙整後的完整字串
        """
        from utils.video_utils import build_video_message, write_frames_to_video

        _fps       = fps         if fps         is not None else self.settings.video_fps
        _max_tok   = max_tokens  if max_tokens  is not None else self.settings.default_max_tokens
        _temp      = temperature if temperature is not None else self.settings.vision_temperature
        chunk_size = self.settings.max_video_frames_per_chunk
        frame_size = self.settings.max_video_frame_size
        quality    = self.settings.video_frame_quality

        # 抽幀切分（同步 I/O，用 executor 避免阻塞 loop）
        loop = asyncio.get_running_loop()
        chunks, info, plan = await loop.run_in_executor(
            None,
            lambda: self._load_video_chunks(video_path, _fps, chunk_size, frame_size, quality),
        )

        total = plan.total_sampled_frames
        print(f"[Video] 影片資訊: {info.duration_sec:.1f}s  "
              f"抽樣幀數={total}  "
              f"分段={plan.num_chunks}  "
              f"(分段上限={chunk_size} 幀/段)")

        # === 單段：直接將原始影片路徑傳出（file:// URL）===
        if plan.num_chunks == 1:
            msg = build_video_message(text, video_path)
            return await self.achat(
                messages=[msg],
                max_tokens=_max_tok,
                temperature=_temp,
                stream=stream,
                **kwargs,
            )

        # === 多段：每段寫 temp MP4 再推論，最後彙整 ===
        import tempfile
        import os
        temp_files: list[str] = []
        summaries: list[str] = []
        
        async def process_chunk(chunk):
            """處理單個影片區塊（非同步）"""
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="vllm_chunk_")
            os.close(tmp_fd)
            temp_files.append(tmp_path)
            
            try:
                # 寫 temp 影片檔（同步，用 executor 避免阻塞 loop）
                await loop.run_in_executor(
                    None, lambda: write_frames_to_video(chunk.frames_b64, tmp_path, fps=_fps)
                )

                chunk_prompt = self._build_chunk_prompt(chunk, text)
                print(f"[Video] 推論第 {chunk.chunk_index}/{plan.num_chunks} 段"
                      f" ({len(chunk.frames_b64)} 幀, "
                      f"{chunk.start_sec:.1f}s~{chunk.end_sec:.1f}s)...")
                msg = build_video_message(chunk_prompt, tmp_path)
                resp = await self.achat(
                    messages=[msg],
                    max_tokens=_max_tok,
                    temperature=_temp,
                    stream=False,
                    **kwargs,
                )
                summary = resp.choices[0].message.content or ""
                print(f"[Video] 第 {chunk.chunk_index} 段完成，摘要 {len(summary)} 字")
                return chunk.chunk_index, summary
            except Exception as e:
                print(f"[Video] 第 {chunk.chunk_index} 段處理失敗: {e}")
                return chunk.chunk_index, f"[處理失敗: {str(e)}]"
        
        try:
            # 並行處理所有區塊
            print(f"[Video] 並行處理 {plan.num_chunks} 個影片區塊...")
            tasks = [process_chunk(chunk) for chunk in chunks]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            
            # 按照 chunk_index 排序結果
            sorted_results = sorted(results, key=lambda x: x[0])
            summaries = [summary for _, summary in sorted_results]
            
        finally:
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    # Ignore errors when deleting temporary video chunks
                    pass

        # 彙整段
        merge_prompt = self._build_merge_prompt(summaries, text, plan.num_chunks)
        print(f"[Video] 彙整 {plan.num_chunks} 段結果...")
        return await self.achat(
            messages=[{"role": "user", "content": merge_prompt}],
            max_tokens=_max_tok,
            temperature=_temp,
            stream=stream,
            **kwargs,
        )

    async def achat_with_video_simple(
        self,
        text: str,
        video_path: Union[str, Path],
        **kwargs,
    ) -> str:
        """
        影片對話異步簡化版，回傳字串。
        """
        response = await self.achat_with_video(
            text=text,
            video_path=video_path,
            stream=False,
            **kwargs,
        )
        if isinstance(response, str):
            return response
        return response.choices[0].message.content or ""

    async def achat_with_video_stream(
        self,
        text: str,
        video_path: Union[str, Path],
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        影片對話異步流式輸出。

        Yields:
            逐步生成的文字片段
        """
        response = await self.achat_with_video(
            text=text,
            video_path=video_path,
            stream=True,
            **kwargs,
        )
        if isinstance(response, str):
            yield response
            return
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ============================================================
    # 工具方法
    # ============================================================

    def list_models(self) -> list[str]:
        """列出可用模型"""
        models = self._sync_client.models.list()
        return [m.id for m in models.data]

    async def alist_models(self) -> list[str]:
        """異步列出可用模型"""
        models = await self._async_client.models.list()
        return [m.id for m in models.data]

    def close(self) -> None:
        """關閉客戶端連線"""
        self._sync_client.close()

    async def aclose(self) -> None:
        """異步關閉客戶端連線"""
        await self._async_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


# ============================================================
# 便捷函式 - 直接呼叫
# ============================================================


def quick_chat(prompt: str, **kwargs) -> str:
    """
    快速對話 - 一行呼叫模型

    用法:
        from api.client import quick_chat
        answer = quick_chat("什麼是機器學習？")
    """
    with ModelClient() as client:
        return client.chat_simple(prompt, **kwargs)


async def aquick_chat(prompt: str, **kwargs) -> str:
    """
    異步快速對話

    用法:
        from api.client import aquick_chat
        answer = await aquick_chat("什麼是機器學習？")
    """
    async with ModelClient() as client:
        return await client.achat_simple(prompt, **kwargs)


# ============================================================
# 獨立執行示範
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  vLLM API 客戶端測試")
    print("=" * 60)

    client = ModelClient()
    prompt = "請用繁體中文簡要說明什麼是大語言模型？"

    print(f"\n[Prompt] {prompt}")
    print(f"[Model]  {client.model_name}")
    print("\n[回應]")

    # 流式輸出
    for text in client.chat_stream(prompt, max_tokens=256):
        print(text, end="", flush=True)
    print("\n")
