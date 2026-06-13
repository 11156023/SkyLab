"""
圖片工具 - 圖片編碼、調整大小、格式轉換
支援視覺模型的圖片輸入處理
"""

import logging
import base64
import io
from pathlib import Path
from typing import Union, List

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL 未安裝，圖片調整大小功能將不可用（安裝: pip install pillow）")


def image_to_base64(image_path: Union[str, Path]) -> str:
    """
    將圖片檔案轉換為 Base64 編碼字串
    
    Args:
        image_path: 圖片檔案路徑
        
    Returns:
        Base64 編碼的字串
        
    Raises:
        FileNotFoundError: 檔案不存在
        
    Examples:
        >>> b64 = image_to_base64("image.jpg")
        >>> print(b64[:50])
        '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDA...'
    """
    image_path = Path(image_path)
    
    if not image_path.exists():
        raise FileNotFoundError(f"圖片檔案不存在: {image_path}")
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()
        return base64.b64encode(image_bytes).decode("utf-8")


def resize_image(
    image_path: Union[str, Path],
    max_size: int = 1024,
    quality: int = 85
) -> str:
    """
    調整圖片大小並轉換為 Base64
    
    保持圖片比例，將長邊縮放到 max_size
    
    Args:
        image_path: 圖片檔案路徑
        max_size: 最大尺寸（長邊）
        quality: JPEG 品質 (1-100)
        
    Returns:
        Base64 編碼的字串
        
    Raises:
        ImportError: PIL 未安裝
        FileNotFoundError: 檔案不存在
        
    Examples:
        >>> b64 = resize_image("large_image.jpg", max_size=512)
    """
    if not PIL_AVAILABLE:
        raise ImportError(
            "PIL (Pillow) 未安裝，無法調整圖片大小\n"
            "請執行: pip install pillow"
        )
    
    image_path = Path(image_path)
    
    if not image_path.exists():
        raise FileNotFoundError(f"圖片檔案不存在: {image_path}")
    
    # 開啟圖片
    img = Image.open(image_path)
    
    # 轉換 RGBA 到 RGB（避免透明背景問題）
    if img.mode == "RGBA":
        # 創建白色背景
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])  # 使用 alpha 通道作為遮罩
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    
    # 調整大小（保持比例）
    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    
    # 轉換為 Base64
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    image_bytes = buffer.getvalue()
    
    return base64.b64encode(image_bytes).decode("utf-8")


def get_image_mime_type(image_path: Union[str, Path]) -> str:
    """
    獲取圖片的 MIME 類型
    
    Args:
        image_path: 圖片檔案路徑
        
    Returns:
        MIME 類型字串 (如 "image/jpeg")
        
    Examples:
        >>> get_image_mime_type("photo.jpg")
        'image/jpeg'
    """
    image_path = Path(image_path)
    suffix = image_path.suffix.lower()
    
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    
    return mime_map.get(suffix, "image/jpeg")


def create_image_content(
    image_path: Union[str, Path],
    resize: bool = True,
    max_size: int = 1024,
    detail: str = "auto"
) -> dict:
    """
    創建 OpenAI 格式的圖片內容
    
    Args:
        image_path: 圖片檔案路徑
        resize: 是否調整大小
        max_size: 最大尺寸
        detail: 詳細程度 ("auto", "low", "high")
        
    Returns:
        OpenAI 格式的圖片內容字典
        
    Examples:
        >>> content = create_image_content("image.jpg")
        >>> print(content["type"])
        'image_url'
    """
    # 獲取 Base64 編碼
    if resize and PIL_AVAILABLE:
        b64_string = resize_image(image_path, max_size=max_size)
    else:
        b64_string = image_to_base64(image_path)
    
    # 獲取 MIME 類型
    mime_type = get_image_mime_type(image_path)
    
    # 構建 OpenAI 格式
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{b64_string}",
            "detail": detail  # auto, low, high
        }
    }


def create_multimodal_content(
    text: str,
    image_paths: List[Union[str, Path]],
    resize: bool = True,
    max_size: int = 1024
) -> List[dict]:
    """
    創建多模態內容（文字 + 圖片）
    
    Args:
        text: 文字提示
        image_paths: 圖片路徑列表
        resize: 是否調整圖片大小
        max_size: 最大圖片尺寸
        
    Returns:
        OpenAI 格式的多模態內容列表
        
    Examples:
        >>> content = create_multimodal_content(
        ...     "描述這張圖片",
        ...     ["image1.jpg", "image2.jpg"]
        ... )
        >>> len(content)
        3  # 1 text + 2 images
    """
    content = []
    
    # 添加文字
    if text:
        content.append({
            "type": "text",
            "text": text
        })
    
    # 添加圖片
    for image_path in image_paths:
        try:
            image_content = create_image_content(
                image_path,
                resize=resize,
                max_size=max_size
            )
            content.append(image_content)
        except Exception as e:
            print(f"[Warning] 無法處理圖片 {image_path}: {e}")
            continue
    
    return content


def create_multimodal_content_from_base64(
    text: str,
    image_base64: str,
    mime_type: str = "image/jpeg"
) -> List[dict]:
    """
    從已編碼的 Base64 創建多模態內容
    
    用於處理呼叫端上傳的圖片（已經是 Base64 格式）
    
    Args:
        text: 文字提示
        image_base64: Base64 編碼的圖片字串
        mime_type: 圖片 MIME 類型
        
    Returns:
        OpenAI 格式的多模態內容列表
        
    Examples:
        >>> content = create_multimodal_content_from_base64(
        ...     "這是什麼？",
        ...     "iVBORw0KGgoAAAANS...",
        ...     "image/png"
        ... )
    """
    content = []
    
    # 添加文字
    if text:
        content.append({
            "type": "text",
            "text": text
        })
    
    # 添加圖片
    content.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_base64}",
            "detail": "auto"
        }
    })
    
    return content


def validate_image_path(image_path: Union[str, Path]) -> bool:
    """
    驗證圖片路徑是否有效
    
    Args:
        image_path: 圖片路徑
        
    Returns:
        True 如果有效，否則 False
    """
    image_path = Path(image_path)
    
    if not image_path.exists():
        return False
    
    if not image_path.is_file():
        return False
    
    # 檢查副檔名
    valid_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    if image_path.suffix.lower() not in valid_extensions:
        return False
    
    return True


# ============================================================
# 測試與示範
# ============================================================

if __name__ == "__main__":
    print("=" * 70)
    print("  圖片工具測試")
    print("=" * 70)
    
    # 測試 Base64 編碼（假設有測試圖片）
    test_image = "test_image.jpg"
    
    if Path(test_image).exists():
        print(f"\n測試圖片: {test_image}")
        
        # Base64 編碼
        b64 = image_to_base64(test_image)
        print(f"Base64 長度: {len(b64)}")
        print(f"Base64 前 50 字元: {b64[:50]}...")
        
        # 調整大小
        if PIL_AVAILABLE:
            b64_resized = resize_image(test_image, max_size=512)
            print(f"\n調整後 Base64 長度: {len(b64_resized)}")
            print(f"壓縮比: {len(b64_resized) / len(b64) * 100:.1f}%")
        
        # 創建 OpenAI 格式內容
        content = create_image_content(test_image)
        print("\nOpenAI 格式內容:")
        print(f"  type: {content['type']}")
        print(f"  url 前 50 字元: {content['image_url']['url'][:50]}...")
        
        # 創建多模態內容
        multimodal = create_multimodal_content(
            "這張圖片中有什麼？",
            [test_image]
        )
        print(f"\n多模態內容項目數: {len(multimodal)}")
        print(f"  第 1 項類型: {multimodal[0]['type']}")
        print(f"  第 2 項類型: {multimodal[1]['type']}")
    else:
        print(f"\n[Info] 測試圖片 '{test_image}' 不存在")
        print("[Info] 請提供測試圖片以執行完整測試")
    
    print("\n" + "=" * 70)
    print("  功能檢查")
    print("=" * 70)
    print(f"PIL 可用: {PIL_AVAILABLE}")
    print("支援的格式: .jpg, .jpeg, .png, .gif, .bmp, .webp")
    print()
