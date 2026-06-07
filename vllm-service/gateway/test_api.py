"""
API Gateway 測試腳本 - 測試 FastAPI 服務的各個端點
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加專案根目錄到路徑
sys.path.append(str(Path(__file__).resolve().parent.parent))

import asyncio
import httpx
from rich.console import Console
from rich.panel import Panel

console = Console()


async def test_model_info():
    """測試模型資訊端點"""
    console.print("\n[bold cyan]測試 1: 獲取模型資訊[/bold cyan]")
    
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:3000/api/model-info")
        
        if response.status_code == 200:
            data = response.json()
            console.print(Panel(
                f"✅ 成功\n\n"
                f"模型: {data['model_name']}\n"
                f"視覺模型: {'是' if data['is_vision_model'] else '否'}\n"
                f"API Base: {data['api_base']}",
                title="模型資訊",
                border_style="green"
            ))
            return data
        else:
            console.print(f"❌ 失敗: {response.status_code}")
            return None


async def test_text_chat():
    """測試文字聊天（非流式）"""
    console.print("\n[bold cyan]測試 2: 文字聊天（非流式）[/bold cyan]")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:3000/api/chat",
            json={
                "message": "請用一句話介紹什麼是人工智慧",
                "max_tokens": 100,
                "temperature": 0.7,
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            console.print(Panel(
                f"✅ 成功\n\n{data['response']}",
                title="AI 回應",
                border_style="green"
            ))
        else:
            console.print(f"❌ 失敗: {response.status_code}")


async def test_text_chat_stream():
    """測試文字聊天（流式）"""
    console.print("\n[bold cyan]測試 3: 文字聊天（流式 SSE）[/bold cyan]")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            "http://localhost:3000/api/chat/stream",
            json={
                "message": "請用三句話介紹機器學習",
                "max_tokens": 150,
                "temperature": 0.7,
            }
        ) as response:
            if response.status_code == 200:
                console.print("✅ 開始接收流式回應:\n", style="green")
                
                full_response = ""
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        
                        if data == "[DONE]":
                            break
                        
                        if data.startswith("[ERROR]"):
                            console.print(f"\n❌ 錯誤: {data[8:]}", style="red")
                            break
                        
                        console.print(data, end="", style="cyan")
                        full_response += data
                
                console.print("\n\n✅ 流式回應完成", style="green")
            else:
                console.print(f"❌ 失敗: {response.status_code}")


async def test_vision_chat(image_path: str | Path):
    """測試視覺聊天（需要圖片）"""
    console.print("\n[bold cyan]測試 4: 視覺聊天（圖片辨識）[/bold cyan]")
    
    image_path = Path(image_path)
    
    if not image_path.exists():
        console.print(f"⚠️  跳過: 圖片檔案不存在 {image_path}", style="yellow")
        return
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(image_path, "rb") as f:
            files = {"image": (image_path.name, f, "image/jpeg")}
            data = {
                "message": "請描述這張圖片的內容",
                "max_tokens": 300,
                "temperature": 0.7,
            }
            
            response = await client.post(
                "http://localhost:3000/api/chat/vision",
                files=files,
                data=data,
            )
        
        if response.status_code == 200:
            result = response.json()
            console.print(Panel(
                f"✅ 成功\n\n{result['response']}",
                title=f"視覺分析: {image_path.name}",
                border_style="green"
            ))
        elif response.status_code == 400:
            console.print("⚠️  當前模型不支援視覺輸入", style="yellow")
        else:
            console.print(f"❌ 失敗: {response.status_code}")


async def main():
    console.print(Panel.fit(
        "[bold magenta]vLLM API Gateway 測試[/bold magenta]",
        border_style="magenta"
    ))
    
    # 檢查後端是否運行
    try:
        async with httpx.AsyncClient() as client:
            await client.get("http://localhost:3000/")
    except httpx.ConnectError:
        console.print("\n❌ 無法連接到後端服務 (http://localhost:3000)", style="red")
        console.print("請先啟動 Gateway: python gateway/main.py", style="yellow")
        return
    
    # 運行測試
    model_info = await test_model_info()
    
    await test_text_chat()
    
    await test_text_chat_stream()
    
    # 如果是視覺模型，測試圖片功能
    if model_info and model_info.get('is_vision_model'):
        # 尋找測試圖片
        test_images = [
            Path("test_image.jpg"),
            Path("test.png"),
            Path("sample.jpg"),
        ]
        
        for img in test_images:
            if img.exists():
                await test_vision_chat(img)
                break
        else:
            console.print(
                "\n⚠️  未找到測試圖片，跳過視覺測試",
                style="yellow"
            )
    
    console.print(Panel.fit(
        "[bold green]✅ 測試完成！[/bold green]",
        border_style="green"
    ))


if __name__ == "__main__":
    asyncio.run(main())
