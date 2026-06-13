"""
影片工具 - 影片幀抽取、分段、編碼處理
支援視覺模型的影片輸入處理（分段切割避免 OOM）

依賴: opencv-python-headless (pip install opencv-python-headless)
"""

from __future__ import annotations

import logging
import base64
import importlib.util
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("opencv-python-headless 未安裝，影片功能不可用（安裝: pip install opencv-python-headless）")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ============================================================
# 資料結構
# ============================================================

@dataclass
class VideoInfo:
    """影片基本資訊"""
    path: str
    duration_sec: float          # 總時長（秒）
    total_frames: int            # 原始總幀數
    native_fps: float            # 原始 FPS
    width: int                   # 寬度（px）
    height: int                  # 高度（px）
    codec: str = ""              # 編碼格式


@dataclass
class ChunkPlan:
    """分段計劃"""
    total_sampled_frames: int    # 抽樣後的總幀數
    chunk_size: int              # 每段幀數上限
    num_chunks: int              # 段數
    use_chunked: bool            # 是否需要分段


@dataclass
class VideoChunk:
    """單一影片段"""
    chunk_index: int             # 段的索引（從 1 開始）
    total_chunks: int            # 總段數
    frames_b64: List[str]        # 幀的 Base64 列表（含 data URI 前綴）
    start_frame: int             # 在抽樣幀序列中的起始索引
    end_frame: int               # 在抽樣幀序列中的結束索引（含）
    start_sec: float             # 時間起點（秒）
    end_sec: float               # 時間終點（秒）


# ============================================================
# 影片資訊
# ============================================================

def get_video_info(video_path: str | Path) -> VideoInfo:
    """
    取得影片的基本資訊。

    Args:
        video_path: 影片檔案路徑

    Returns:
        VideoInfo 資料類別

    Raises:
        ImportError: opencv 未安裝
        FileNotFoundError: 影片不存在
        RuntimeError: 影片無法開啟
    """
    if not CV2_AVAILABLE:
        raise ImportError("需要 opencv-python-headless: pip install opencv-python-headless")

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"影片不存在: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片: {video_path}")

    try:
        native_fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration_sec = total_frames / native_fps if native_fps > 0 else 0.0

        # 嘗試取得編碼資訊（整數轉 4 字元 codec 字串）
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join([chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]).strip()

        return VideoInfo(
            path=str(video_path),
            duration_sec=round(duration_sec, 2),
            total_frames=total_frames,
            native_fps=round(native_fps, 2),
            width=width,
            height=height,
            codec=codec,
        )
    finally:
        cap.release()


# ============================================================
# 幀抽取
# ============================================================

def _frame_to_base64(
    frame_bgr,            # numpy.ndarray (BGR)
    max_size: int = 768,
    quality: int = 80,
) -> str:
    """
    將 OpenCV BGR 幀轉換為 Base64 data URI。
    內部使用 PIL 進行 resize（如可用），否則用 OpenCV resize。
    """
    # BGR → RGB
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    if PIL_AVAILABLE:
        img = Image.fromarray(frame_rgb)
        # 保持比例縮放到 max_size
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    else:
        # 用 OpenCV resize（無 PIL）
        h, w = frame_rgb.shape[:2]
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            frame_rgb = cv2.resize(frame_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        # RGB → BGR for imencode
        frame_bgr_resized = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode(".jpg", frame_bgr_resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
        b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

    return f"data:image/jpeg;base64,{b64}"


def extract_frames(
    video_path: str | Path,
    fps: float = 1.0,
    max_frames: Optional[int] = None,
    max_size: int = 768,
    quality: int = 80,
) -> List[str]:
    """
    從影片抽取幀並轉換為 Base64 data URI 列表。

    Args:
        video_path:  影片路徑
        fps:         抽幀速率（幀/秒）,0 表示取全部原始幀
        max_frames:  最大幀數上限（None 表示不限制）
        max_size:    每幀的最長邊縮放尺寸（px）
        quality:     JPEG 品質（1–100）

    Returns:
        Base64 data URI 字串列表

    Raises:
        ImportError: opencv 未安裝
        FileNotFoundError: 影片不存在
    """
    if not CV2_AVAILABLE:
        raise ImportError("需要 opencv-python-headless: pip install opencv-python-headless")

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"影片不存在: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片: {video_path}")

    try:
        native_fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # 計算要抽取的幀的「原生幀索引」列表
        if fps <= 0 or fps >= native_fps:
            # 取全部幀
            frame_indices = list(range(total_frames))
        else:
            interval = native_fps / fps          # 每隔幾幀取一幀
            frame_indices = [
                int(round(i * interval))
                for i in range(int(total_frames / interval) + 1)
                if int(round(i * interval)) < total_frames
            ]

        # 去重（浮點 round 可能重複）並排序
        frame_indices = sorted(set(frame_indices))

        # 限制 max_frames
        if max_frames and len(frame_indices) > max_frames:
            # 均勻子取樣（保留首尾）
            step = len(frame_indices) / max_frames
            frame_indices = [frame_indices[int(i * step)] for i in range(max_frames)]

        frames_b64: List[str] = []
        prev_idx = -1

        for idx in frame_indices:
            # 若索引連續則直接讀；否則 seek
            if idx != prev_idx + 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)

            ret, frame = cap.read()
            if not ret:
                continue

            b64 = _frame_to_base64(frame, max_size=max_size, quality=quality)
            frames_b64.append(b64)
            prev_idx = idx

        return frames_b64

    finally:
        cap.release()


# ============================================================
# 分段計劃
# ============================================================

def plan_chunks(
    total_frames: int,
    chunk_size: int,
) -> ChunkPlan:
    """
    計算分段方案。

    Args:
        total_frames: 已抽樣後的總幀數
        chunk_size:   每段最大幀數

    Returns:
        ChunkPlan
    """
    if chunk_size <= 0:
        chunk_size = total_frames  # 不切分

    use_chunked  = total_frames > chunk_size
    num_chunks   = math.ceil(total_frames / chunk_size) if use_chunked else 1

    return ChunkPlan(
        total_sampled_frames=total_frames,
        chunk_size=chunk_size,
        num_chunks=num_chunks,
        use_chunked=use_chunked,
    )


def chunk_frames(
    frames_b64: List[str],
    chunk_size: int,
    video_info: Optional[VideoInfo] = None,
    sample_fps: float = 1.0,
) -> List[VideoChunk]:
    """
    將幀列表切分成多個 VideoChunk。

    Args:
        frames_b64:   Base64 幀列表
        chunk_size:   每段幀數上限
        video_info:   影片資訊（用於換算時間戳，可為 None）
        sample_fps:   抽幀使用的 fps（用於換算時間戳）

    Returns:
        VideoChunk 列表
    """
    total = len(frames_b64)
    if total == 0:
        return []

    num_chunks = math.ceil(total / chunk_size)
    chunks: List[VideoChunk] = []

    for i in range(num_chunks):
        start = i * chunk_size
        end   = min(start + chunk_size, total) - 1          # 含
        subset = frames_b64[start : end + 1]

        # 換算時間（每幀間隔 = 1 / sample_fps 秒）
        frame_interval = 1.0 / sample_fps if sample_fps > 0 else 1.0
        start_sec = round(start * frame_interval, 2)
        end_sec   = round(end   * frame_interval, 2)

        chunks.append(VideoChunk(
            chunk_index=i + 1,
            total_chunks=num_chunks,
            frames_b64=subset,
            start_frame=start,
            end_frame=end,
            start_sec=start_sec,
            end_sec=end_sec,
        ))

    return chunks


# ============================================================
# 建立 API content 物件
# ============================================================

def write_frames_to_video(
    frames_b64: List[str],
    output_path: str | Path,
    fps: float = 1.0,
) -> str:
    """
    將 Base64 JPEG 幀列表寫入 MP4 影片檔案。
    用於分段推論時建立 temp 影片檔。

    Args:
        frames_b64:  Base64 data URI 幀列表（data:image/jpeg;base64,...）
        output_path: 輸出 MP4 路徑
        fps:         輸出影片的幀率

    Returns:
        輸出路徑字串
    """
    if not CV2_AVAILABLE:
        raise ImportError("需要 opencv-python-headless: pip install opencv-python-headless")
    if not frames_b64:
        raise ValueError("幀列表不能為空")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import numpy as _np  # cv2 依賴 numpy，此處 import 必然成功

    def _decode(data_uri: str):
        """將 base64 data URI 解碼為 BGR numpy array"""
        b64_data = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
        img_bytes = base64.b64decode(b64_data)
        arr = _np.frombuffer(img_bytes, dtype=_np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)   # BGR

    first = _decode(frames_b64[0])
    if first is None:
        raise RuntimeError(f"無法解碼第一幀: {frames_b64[0][:80]}...")

    h, w = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, float(fps), (w, h))

    writer.write(first)
    for uri in frames_b64[1:]:
        frame = _decode(uri)
        if frame is not None:
            writer.write(frame)

    writer.release()
    return str(output_path)


def create_video_content(video_path: str | Path, use_file_url: bool = False) -> dict:
    """
    建立符合 vLLM video_url 格式的 content 物件。

    vLLM OpenAI 相容 API 接受的格式：
        {
            "type": "video_url",
            "video_url": {"url": "data:video/mp4;base64,..."}
        }
    URL 支援: data: / file:// (需 --allowed-local-media-path) / http(s)://

    預設使用 data: URL，將影片二進位 base64 編碼後直接嵌入請求，
    無須 vLLM 伺服器設定 --allowed-local-media-path。

    Args:
        video_path: 影片路徑
        use_file_url: True 時改用 file:// URL（需要 vLLM 啟動時有 --allowed-local-media-path）

    Returns:
        video_url content dict
    """
    abs_path = Path(video_path).resolve()

    if use_file_url:
        url = f"file://{abs_path}"
    else:
        # 將影片二進位讀取後 base64 編碼為 data URL
        with open(abs_path, "rb") as fh:
            video_bytes = fh.read()
        b64 = base64.b64encode(video_bytes).decode("utf-8")
        # 嘗試依副檔名決定 MIME type
        suffix = abs_path.suffix.lower().lstrip(".")
        mime_map = {"mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
                    "avi": "video/x-msvideo", "mov": "video/quicktime"}
        mime = mime_map.get(suffix, "video/mp4")
        url = f"data:{mime};base64,{b64}"

    return {
        "type": "video_url",
        "video_url": {"url": url},
    }


def build_video_message(
    text: str,
    video_path: str | Path,
    use_file_url: bool = False,
) -> dict:
    """
    建立包含影片（video_url）和文字的完整 user message。

    Args:
        text:         使用者文字提示
        video_path:   影片路徑
        use_file_url: True 時使用 file:// URL（需要 --allowed-local-media-path）；
                      預設使用 data: URL（嵌入式，無需額外 server 設定）

    Returns:
        {"role": "user", "content": [...]}
    """
    content = [
        create_video_content(video_path, use_file_url=use_file_url),
        {"type": "text", "text": text},
    ]
    return {"role": "user", "content": content}


# ============================================================
# 高層便利函式
# ============================================================

def prepare_video_chunks(
    video_path: str | Path,
    fps: float = 1.0,
    chunk_size: int = 64,
    max_size: int = 768,
    quality: int = 80,
) -> Tuple[List[VideoChunk], VideoInfo, ChunkPlan]:
    """
    一次完成：抽幀 → 分段計劃 → 切分 VideoChunk。

    Args:
        video_path: 影片路徑
        fps:        抽幀速率（幀/秒）
        chunk_size: 每段幀數上限
        max_size:   幀縮放尺寸（px）
        quality:    JPEG 品質

    Returns:
        (chunks列表, VideoInfo, ChunkPlan)

    Examples:
        >>> chunks, info, plan = prepare_video_chunks("video.mp4", fps=1.0, chunk_size=64)
        >>> print(f"共 {plan.num_chunks} 段, 每段最多 {plan.chunk_size} 幀")
        >>> for chunk in chunks:
        ...     print(f"  Chunk {chunk.chunk_index}: {len(chunk.frames_b64)} 幀")
    """
    info = get_video_info(video_path)

    # 抽幀
    frames_b64 = extract_frames(
        video_path,
        fps=fps,
        max_size=max_size,
        quality=quality,
    )

    # 計劃
    plan = plan_chunks(len(frames_b64), chunk_size)

    # 切分
    chunks = chunk_frames(
        frames_b64,
        chunk_size=chunk_size,
        video_info=info,
        sample_fps=fps,
    )

    return chunks, info, plan


# ============================================================
# 獨立執行測試
# ============================================================

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("  影片工具測試")
    print("=" * 70)

    print(f"\nOpenCV 可用: {CV2_AVAILABLE}")
    print(f"PIL    可用: {PIL_AVAILABLE}")

    if len(sys.argv) < 2:
        print("\n用法: python video_utils.py <影片路徑> [fps] [chunk_size]")
        print("範例: python video_utils.py video.mp4 1.0 64")
        sys.exit(0)

    vpath      = sys.argv[1]
    fps_arg    = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    chunk_arg  = int(sys.argv[3])   if len(sys.argv) > 3 else 64

    print(f"\n影片路徑 : {vpath}")
    print(f"抽幀 FPS : {fps_arg}")
    print(f"分段上限 : {chunk_arg} 幀/段")

    # 取得影片資訊
    try:
        info = get_video_info(vpath)
        print("\n--- 影片資訊 ---")
        print(f"  時長      : {info.duration_sec:.1f} 秒")
        print(f"  原始幀數  : {info.total_frames}")
        print(f"  原始 FPS  : {info.native_fps}")
        print(f"  解析度    : {info.width} × {info.height}")
        print(f"  編碼      : {info.codec}")
    except Exception as e:
        print(f"[錯誤] 無法讀取影片資訊: {e}")
        sys.exit(1)

    # 分段制劃
    chunks, info, plan = prepare_video_chunks(
        vpath,
        fps=fps_arg,
        chunk_size=chunk_arg,
        max_size=768,
        quality=80,
    )

    print("\n--- 抽幀結果 ---")
    print(f"  抽樣幀數  : {plan.total_sampled_frames}")
    print(f"  需要分段  : {'是' if plan.use_chunked else '否'}")
    print(f"  總段數    : {plan.num_chunks}")

    for c in chunks:
        size_kb = sum(len(f) for f in c.frames_b64) * 3 // 4 // 1024
        print(f"  Chunk {c.chunk_index}/{c.total_chunks}: "
              f"{len(c.frames_b64)} 幀  "
              f"[{c.start_sec:.1f}s – {c.end_sec:.1f}s]  "
              f"約 {size_kb} KB")
