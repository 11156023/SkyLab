"""
模型工具 - 模型類型檢測與資訊獲取
"""

from typing import Literal

ModelType = Literal["vision", "text"]


# 視覺模型關鍵字列表
VISION_KEYWORDS = [
    "vl",          # Visual Language
    "vision",      # Vision
    "visual",      # Visual
    "multimodal",  # Multimodal
    "llava",       # LLaVA 系列
    "qwen-vl",     # Qwen-VL 系列
    "qwenvl",      # Qwen-VL 變體
    "cogvlm",      # CogVLM 系列
    "internvl",    # InternVL 系列
    "mplug",       # mPLUG 系列
    "blip",        # BLIP 系列
    "fuyu",        # Fuyu 系列
    "kosmos",      # Kosmos 系列
    "paligemma",   # PaliGemma
    "phi-vision",  # Phi-Vision
    "idefics",     # IDEFICS
]


def detect_model_type(model_name: str) -> ModelType:
    """
    檢測模型類型
    
    Args:
        model_name: 模型名稱或路徑
        
    Returns:
        "vision": 視覺-語言模型 (VL Model)
        "text": 純文字模型 (Text-only Model)
        
    Examples:
        >>> detect_model_type("Qwen3-VL-30B-A3B-Thinking-FP8")
        'vision'
        >>> detect_model_type("Qwen3-235B-A22B-NVFP4")
        'text'
        >>> detect_model_type("meta-llama/Llama-3.1-70B")
        'text'
        >>> detect_model_type("liuhaotian/llava-v1.6-34b")
        'vision'
    """
    model_lower = model_name.lower()
    
    # 檢查是否包含視覺模型關鍵字
    for keyword in VISION_KEYWORDS:
        if keyword in model_lower:
            return "vision"
    
    return "text"


def is_vision_model(model_name: str) -> bool:
    """
    判斷是否為視覺模型
    
    Args:
        model_name: 模型名稱或路徑
        
    Returns:
        True 如果是視覺模型，否則 False
        
    Examples:
        >>> is_vision_model("Qwen3-VL-30B-A3B-Thinking-FP8")
        True
        >>> is_vision_model("Qwen3-235B-A22B-NVFP4")
        False
    """
    return detect_model_type(model_name) == "vision"


def is_text_model(model_name: str) -> bool:
    """
    判斷是否為純文字模型
    
    Args:
        model_name: 模型名稱或路徑
        
    Returns:
        True 如果是純文字模型，否則 False
    """
    return detect_model_type(model_name) == "text"


def get_model_info(model_name: str) -> dict[str, str]:
    """
    獲取模型資訊
    
    Args:
        model_name: 模型名稱或路徑
        
    Returns:
        包含模型資訊的字典
        
    Examples:
        >>> get_model_info("Qwen3-VL-30B-A3B-Thinking-FP8")
        {
            'name': 'Qwen3-VL-30B-A3B-Thinking-FP8',
            'type': 'vision',
            'supports_image': True,
            'supports_text': True
        }
    """
    model_type = detect_model_type(model_name)
    
    return {
        "name": model_name,
        "type": model_type,
        "supports_image": model_type == "vision",
        "supports_text": True,
    }


# ============================================================
# 測試與驗證
# ============================================================

if __name__ == "__main__":
    # 測試案例
    test_models = [
        # 視覺模型
        "Qwen3-VL-30B-A3B-Thinking-FP8",
        "Qwen/Qwen-VL-Chat",
        "liuhaotian/llava-v1.6-34b",
        "THUDM/cogvlm2-llama3-chat-19B",
        "OpenGVLab/InternVL-Chat-V1-5",
        
        # 純文字模型
        "Qwen3-235B-A22B-NVFP4",
        "meta-llama/Llama-3.1-70B-Instruct",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "nvidia/Qwen3-235B-A22B-NVFP4",
    ]
    
    print("=" * 70)
    print("  模型類型檢測測試")
    print("=" * 70)
    
    for model in test_models:
        model_type = detect_model_type(model)
        is_vl = is_vision_model(model)
        icon = "🖼️ " if is_vl else "📝"
        print(f"{icon} {model:<50} -> {model_type}")
    
    print("\n" + "=" * 70)
    print("  模型資訊獲取測試")
    print("=" * 70)
    
    info = get_model_info("Qwen3-VL-30B-A3B-Thinking-FP8")
    print(f"\n模型名稱: {info['name']}")
    print(f"模型類型: {info['type']}")
    print(f"支援圖片: {info['supports_image']}")
    print(f"支援文字: {info['supports_text']}")
    print()
