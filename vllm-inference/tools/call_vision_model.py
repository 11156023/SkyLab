"""
視覺模型使用範例
演示如何使用 Qwen3-VL 等視覺-語言模型處理圖片輸入
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.client import ModelClient


def demo_vision_simple():
    """簡單視覺對話範例"""
    print("=" * 70)
    print("  範例 1: 單張圖片描述")
    print("=" * 70)
    
    client = ModelClient()
    
    # 檢查是否為視覺模型
    if not client.is_vision_model:
        print(f"\n[警告] 當前模型 '{client.settings.model_name}' 不是視覺模型")
        print("[提示] 請在 .env 中設定視覺模型，例如:")
        print("       MODEL_NAME=Qwen3-VL-30B-A3B-Thinking-FP8")
        return
    
    print(f"\n模型: {client.settings.model_name}")
    print("視覺模型: ✓")
    
    # 範例圖片路徑（請替換為實際圖片）
    image_path = "test_image.jpg"
    
    if not Path(image_path).exists():
        print(f"\n[Info] 測試圖片 '{image_path}' 不存在")
        print("[提示] 請提供測試圖片以執行完整演示")
        return
    
    # 視覺對話
    prompt = "請詳細描述這張圖片的內容，包括主要物體、顏色、場景等。"
    print(f"\n[提示] {prompt}")
    print(f"[圖片] {image_path}")
    print("\n[回應]")
    
    try:
        answer = client.chat_with_image_simple(
            text=prompt,
            image_paths=image_path,
            max_tokens=512,
        )
        print(answer)
    except Exception as e:
        print(f"[錯誤] {e}")
        import traceback
        traceback.print_exc()


def demo_vision_multiple_images():
    """多張圖片對話範例"""
    print("\n" + "=" * 70)
    print("  範例 2: 多張圖片比較")
    print("=" * 70)
    
    client = ModelClient()
    
    if not client.is_vision_model:
        print("\n[跳過] 非視覺模型")
        return
    
    # 多張圖片
    image_paths = ["image1.jpg", "image2.jpg"]
    
    # 檢查圖片是否存在
    existing_images = [p for p in image_paths if Path(p).exists()]
    
    if not existing_images:
        print(f"\n[Info] 測試圖片不存在: {', '.join(image_paths)}")
        return
    
    prompt = "請比較這些圖片的異同點。"
    print(f"\n[提示] {prompt}")
    print(f"[圖片] {', '.join(existing_images)}")
    print("\n[回應]")
    
    try:
        answer = client.chat_with_image_simple(
            text=prompt,
            image_paths=existing_images,
            max_tokens=512,
        )
        print(answer)
    except Exception as e:
        print(f"[錯誤] {e}")


def demo_vision_stream():
    """視覺對話流式輸出範例"""
    print("\n" + "=" * 70)
    print("  範例 3: 流式輸出")
    print("=" * 70)
    
    client = ModelClient()
    
    if not client.is_vision_model:
        print("\n[跳過] 非視覺模型")
        return
    
    image_path = "test_image.jpg"
    
    if not Path(image_path).exists():
        print(f"\n[Info] 測試圖片 '{image_path}' 不存在")
        return
    
    prompt = "請用繁體中文簡要說明這張圖片的主題。"
    print(f"\n[提示] {prompt}")
    print(f"[圖片] {image_path}")
    print("\n[回應] ", end="", flush=True)
    
    try:
        for chunk in client.chat_with_image_stream(
            text=prompt,
            image_paths=image_path,
            max_tokens=256,
        ):
            print(chunk, end="", flush=True)
        print()
    except Exception as e:
        print(f"\n[錯誤] {e}")


async def demo_vision_async():
    """異步視覺對話範例"""
    print("\n" + "=" * 70)
    print("  範例 4: 異步視覺對話")
    print("=" * 70)
    
    client = ModelClient()
    
    if not client.is_vision_model:
        print("\n[跳過] 非視覺模型")
        return
    
    image_path = "test_image.jpg"
    
    if not Path(image_path).exists():
        print(f"\n[Info] 測試圖片 '{image_path}' 不存在")
        return
    
    # 異步處理多個問題
    questions = [
        "這張圖片的主要內容是什麼？",
        "圖片中有哪些顏色？",
        "這張圖片的風格是什麼？",
    ]
    
    print(f"\n[圖片] {image_path}")
    print(f"[併發處理 {len(questions)} 個問題]\n")
    
    try:
        tasks = [
            client.achat_with_image_simple(q, image_path, max_tokens=128)
            for q in questions
        ]
        
        results = await asyncio.gather(*tasks)
        
        for q, r in zip(questions, results):
            print(f"Q: {q}")
            print(f"A: {r}\n")
        
        await client.aclose()
    except Exception as e:
        print(f"[錯誤] {e}")
        import traceback
        traceback.print_exc()


def demo_mixed_usage():
    """混合使用範例 - 自動處理視覺和文字模型"""
    print("\n" + "=" * 70)
    print("  範例 5: 混合使用（自動適配）")
    print("=" * 70)
    
    client = ModelClient()
    
    print(f"\n模型: {client.settings.model_name}")
    print(f"模型類型: {'視覺模型' if client.is_vision_model else '純文字模型'}")
    
    # 嘗試使用視覺輸入
    # 如果是文字模型，會自動忽略圖片
    prompt = "請回答問題"
    image_path = "test_image.jpg"
    
    print(f"\n[提示] {prompt}")
    
    if Path(image_path).exists():
        print(f"[圖片] {image_path}")
    else:
        print("[圖片] (不存在)")
    
    print("\n[回應]")
    
    try:
        # 這個呼叫在視覺模型和文字模型都能工作
        # 文字模型會自動忽略圖片參數
        answer = client.chat_with_image_simple(
            text=prompt,
            image_paths=image_path,
            max_tokens=128,
        )
        print(answer)
        
        if not client.is_vision_model:
            print("\n[Info] 已自動忽略圖片輸入，僅處理文字")
    except Exception as e:
        print(f"[錯誤] {e}")


def show_model_info():
    """顯示模型資訊"""
    print("=" * 70)
    print("  模型資訊")
    print("=" * 70)
    
    client = ModelClient()
    
    print(f"\n模型名稱: {client.settings.model_name}")
    print(f"模型路徑: {client.settings.resolved_model_path}")
    print(f"模型類型: {'視覺模型 🖼️' if client.is_vision_model else '純文字模型 📝'}")
    print(f"支援圖片: {'✓' if client.is_vision_model else '✗'}")
    
    if client.is_vision_model:
        print("\n視覺模型設定:")
        print(f"  最大圖片尺寸: {client.settings.max_image_size}px")
        print(f"  自動調整大小: {'✓' if client.settings.enable_image_resize else '✗'}")
    
    print()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if mode == "info":
        show_model_info()
    elif mode == "simple":
        demo_vision_simple()
    elif mode == "multiple":
        demo_vision_multiple_images()
    elif mode == "stream":
        demo_vision_stream()
    elif mode == "async":
        asyncio.run(demo_vision_async())
    elif mode == "mixed":
        demo_mixed_usage()
    elif mode == "all":
        show_model_info()
        demo_vision_simple()
        demo_vision_multiple_images()
        demo_vision_stream()
        print("\n" + "─" * 70)
        print("  異步範例")
        print("─" * 70)
        asyncio.run(demo_vision_async())
        demo_mixed_usage()
    else:
        print(f"未知模式: {mode}")
        print("可用模式: info, simple, multiple, stream, async, mixed, all")
