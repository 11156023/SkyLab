"""
影片模型使用範例
演示如何使用 Qwen3-VL 等視覺-語言模型處理影片輸入

使用方式:
    python 小工具/call_video_model.py <video_path> [mode]

模式:
    info     - 顯示模型與影片資訊（不進行推論）
    simple   - 簡單影片問答（預設）
    stream   - 流式輸出
    async    - 異步推論
    chunks   - 顯示分塊計劃
    all      - 執行所有範例

範例:
    python 小工具/call_video_model.py video.mp4
    python 小工具/call_video_model.py video.mp4 stream
    python 小工具/call_video_model.py video.mp4 info
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# 修正 Python 路徑（支援從任意目錄執行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.client import ModelClient
from utils.video_utils import get_video_info, plan_chunks, prepare_video_chunks


# ──────────────────────────────────────────────
# 輔助工具
# ──────────────────────────────────────────────

def _divider(title: str = "", width: int = 70) -> None:
    """列印分隔線"""
    if title:
        print("\n" + "=" * width)
        print(f"  {title}")
        print("=" * width)
    else:
        print("─" * width)


def _check_video(video_path: str) -> bool:
    """確認影片存在，否則列印提示"""
    if not Path(video_path).exists():
        print(f"\n[錯誤] 影片檔案不存在: {video_path}")
        print("[提示] 請指定有效的影片路徑，例如:")
        print("       python 小工具/call_video_model.py /path/to/video.mp4")
        return False
    return True


def _check_vision(client: ModelClient) -> bool:
    """確認為視覺模型"""
    if not client.is_vision_model:
        print(f"\n[警告] 當前模型 '{client.settings.model_name}' 不是視覺模型")
        print("[提示] 請在 .env 中設定支援視覺的模型，例如:")
        print("       MODEL_NAME=Qwen3-VL-30B-A3B-Thinking-FP8")
        return False
    return True


# ──────────────────────────────────────────────
# 範例 0: 資訊顯示
# ──────────────────────────────────────────────

def show_info(video_path: str) -> None:
    """顯示模型設定與影片元資料，不進行推論"""
    _divider("模型與影片資訊")

    client = ModelClient()
    settings = client.settings

    print("\n【模型】")
    print(f"  名稱    : {settings.model_name}")
    print(f"  路徑    : {settings.resolved_model_path}")
    print(f"  類型    : {'視覺模型 🖼' if client.is_vision_model else '純文字模型 📝'}")

    print("\n【影片設定】")
    print(f"  採樣 FPS  : {settings.video_fps}")
    print(f"  每段上限  : {settings.max_video_frames_per_chunk} 幀")
    print(f"  幀最大邊長: {settings.max_video_frame_size}px")
    print(f"  JPEG 品質 : {settings.video_frame_quality}")

    if not _check_video(video_path):
        return

    print(f"\n【影片元資料】: {video_path}")
    try:
        info = get_video_info(video_path)
        print(f"  解析度  : {info.width} × {info.height}")
        print(f"  原始 FPS: {info.native_fps:.2f}")
        print(f"  時長    : {info.duration_sec:.1f} 秒")
        print(f"  總幀數  : {info.total_frames}")

        sample_fps = settings.video_fps
        sampled = max(1, int(info.duration_sec * sample_fps))
        print(f"\n【分析預估】（以 {sample_fps} FPS 拝樣）")
        print(f"  預計抽樣幀數: {sampled}")

        chunk_plan = plan_chunks(sampled, settings.max_video_frames_per_chunk)
        print(f"  需要分段數  : {chunk_plan.num_chunks}")
        if chunk_plan.num_chunks > 1:
            last_size = sampled - chunk_plan.chunk_size * (chunk_plan.num_chunks - 1)
            print(f"  每段幀數    : {chunk_plan.chunk_size}")
            print(f"  最後段幀數  : {last_size}")
            print("\n  [注意] 影片較長，將使用分段推論策略（Chunked Inference）")
        else:
            print("  [單段] 影片可一次完整送入模型")
    except Exception as e:
        print(f"  [錯誤] 無法讀取影片: {e}")

    print()


# ──────────────────────────────────────────────
# 範例 1: 簡單影片問答
# ──────────────────────────────────────────────

def demo_video_simple(video_path: str) -> None:
    """基本影片理解範例（自動分塊）"""
    _divider("範例 1: 影片理解（自動分塊）")

    if not _check_video(video_path):
        return

    client = ModelClient()

    if not _check_vision(client):
        return

    prompt = "請詳細描述這段影片的內容，包括主要場景、動作、人物或物體，以及時間順序。"

    print(f"\n模型  : {client.settings.model_name}")
    print(f"影片  : {video_path}")
    print(f"提問  : {prompt}")
    print("\n[推論中...]\n")

    t0 = time.time()
    try:
        answer = client.chat_with_video_simple(
            text=prompt,
            video_path=video_path,
            max_tokens=1024,
        )
        elapsed = time.time() - t0
        print(answer)
        print(f"\n[完成] 耗時 {elapsed:.1f} 秒")
    except Exception as e:
        print(f"[錯誤] {e}")
        import traceback
        traceback.print_exc()


# ──────────────────────────────────────────────
# 範例 2: 流式輸出
# ──────────────────────────────────────────────

def demo_video_stream(video_path: str) -> None:
    """影片推論流式輸出範例"""
    _divider("範例 2: 流式輸出")

    if not _check_video(video_path):
        return

    client = ModelClient()

    if not _check_vision(client):
        return

    prompt = "請用三句話概括這段影片的主要內容。"

    print(f"\n影片  : {video_path}")
    print(f"提問  : {prompt}")
    print("\n[回應] ", end="", flush=True)

    t0 = time.time()
    try:
        for token in client.chat_with_video_stream(
            text=prompt,
            video_path=video_path,
            max_tokens=512,
        ):
            print(token, end="", flush=True)
        elapsed = time.time() - t0
        print(f"\n\n[完成] 耗時 {elapsed:.1f} 秒")
    except Exception as e:
        print(f"\n[錯誤] {e}")
        import traceback
        traceback.print_exc()


# ──────────────────────────────────────────────
# 範例 3: 異步推論
# ──────────────────────────────────────────────

async def demo_video_async(video_path: str) -> None:
    """異步影片推論範例（適合高併發場景）"""
    _divider("範例 3: 異步推論")

    if not _check_video(video_path):
        return

    client = ModelClient()

    if not _check_vision(client):
        return

    questions = [
        "影片的主要主題是什麼？",
        "影片共有哪些場景或段落？",
    ]

    print(f"\n影片: {video_path}")
    print(f"[併發處理 {len(questions)} 個問題]\n")

    t0 = time.time()
    try:
        tasks = [
            client.achat_with_video_simple(q, video_path, max_tokens=256)
            for q in questions
        ]
        results = await asyncio.gather(*tasks)

        for q, r in zip(questions, results):
            print(f"Q: {q}")
            print(f"A: {r}\n")

        elapsed = time.time() - t0
        print(f"[完成] 總耗時 {elapsed:.1f} 秒")
        await client.aclose()
    except Exception as e:
        print(f"[錯誤] {e}")
        import traceback
        traceback.print_exc()


# ──────────────────────────────────────────────
# 範例 4: 顯示分塊計劃（dry-run）
# ──────────────────────────────────────────────

def demo_chunk_plan(video_path: str) -> None:
    """顯示影片分塊細節，不執行推論（用於除錯）"""
    _divider("範例 4: 分塊計劃（dry-run）")

    if not _check_video(video_path):
        return

    client = ModelClient()
    s = client.settings

    print(f"\n影片: {video_path}")
    print(f"採樣 FPS: {s.video_fps}  |  每段上限: {s.max_video_frames_per_chunk} 幀  |  幀尺寸: {s.max_video_frame_size}px")
    print("\n[擷取幀中，請稍候...]\n")

    t0 = time.time()
    try:
        chunks, info, plan = prepare_video_chunks(
            video_path=video_path,
            fps=s.video_fps,
            chunk_size=s.max_video_frames_per_chunk,
            max_size=s.max_video_frame_size,
            quality=s.video_frame_quality,
        )
        elapsed = time.time() - t0

        print("影片資訊:")
        print(f"  解析度  : {info.width} × {info.height}")
        print(f"  時長    : {info.duration_sec:.1f} 秒（{info.total_frames} 幀）")
        print(f"  已拝樣  : {plan.total_sampled_frames} 幀，耗時 {elapsed:.1f} 秒")
        print(f"\n分塊計劃: {plan.num_chunks} 段")

        for chunk in chunks:
            est_tokens = len(chunk.frames_b64) * 512  # ~512 視覺 token/幀
            print(
                f"  段 {chunk.chunk_index}/{chunk.total_chunks}"
                f"  幀: {len(chunk.frames_b64):>3}"
                f"  時間: {chunk.start_sec:.1f}s ~ {chunk.end_sec:.1f}s"
                f"  預估視覺 token: ~{est_tokens:,}"
            )

        total_frames = sum(len(c.frames_b64) for c in chunks)
        total_tokens = total_frames * 512
        print(f"\n總計: {total_frames} 幀  |  預估視覺 token: ~{total_tokens:,}")
        if plan.num_chunks > 1:
            print("[分段策略] 將依序推論每段，最後合併摘要")
    except Exception as e:
        print(f"[錯誤] {e}")
        import traceback
        traceback.print_exc()


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def _usage() -> None:
    print("用法: python 小工具/call_video_model.py <video_path> [mode]")
    print()
    print("模式:")
    print("  info    顯示模型與影片資訊（不推論）")
    print("  simple  簡單影片問答 [預設]")
    print("  stream  流式輸出")
    print("  async   異步推論")
    print("  chunks  分塊計劃 dry-run")
    print("  all     執行所有範例")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        _usage()
        sys.exit(1)

    video_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "simple"

    if mode == "info":
        show_info(video_path)
    elif mode == "simple":
        demo_video_simple(video_path)
    elif mode == "stream":
        demo_video_stream(video_path)
    elif mode == "async":
        asyncio.run(demo_video_async(video_path))
    elif mode == "chunks":
        demo_chunk_plan(video_path)
    elif mode == "all":
        show_info(video_path)
        demo_video_simple(video_path)
        demo_video_stream(video_path)
        demo_chunk_plan(video_path)
        _divider("異步範例")
        asyncio.run(demo_video_async(video_path))
    else:
        print(f"[錯誤] 未知模式: {mode}")
        _usage()
        sys.exit(1)
