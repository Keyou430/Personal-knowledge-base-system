# -*- coding: utf-8 -*-
"""
图片处理器
双通道处理：EasyOCR 提取文字 + Qwen-VL 生成描述
"""

import os
import logging
import base64

import httpx

from config import VISION_API_KEY, VISION_BASE_URL, VISION_MODEL

logger = logging.getLogger(__name__)

# OCR 模块级缓存
_ocr_reader = None


def _get_ocr_reader():
    """获取 EasyOCR 读取器（单例模式，首次加载较慢）"""
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        logger.info("正在加载 EasyOCR 模型（首次运行需下载约 100MB）...")
        _ocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        logger.info("EasyOCR 模型加载完成")
    return _ocr_reader


def extract_text_ocr(image_path: str) -> str:
    """
    使用 EasyOCR 从图片中提取文字

    Args:
        image_path: 图片文件路径

    Returns:
        提取的文字内容
    """
    reader = _get_ocr_reader()
    results = reader.readtext(image_path, detail=0)
    text = "\n".join(results)

    logger.info(f"OCR 提取完成: {len(text)} 个字符")
    return text


def _encode_image_base64(image_path: str) -> str:
    """将图片编码为 base64 字符串"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def describe_image_vlm(image_path: str) -> str:
    """
    使用 Qwen-VL 视觉模型生成图片描述

    Args:
        image_path: 图片文件路径

    Returns:
        图片描述文本
    """
    if not VISION_API_KEY:
        return "（视觉模型未配置，跳过图片描述）"

    base64_image = _encode_image_base64(image_path)
    ext = os.path.splitext(image_path)[1].lower().replace(".", "")
    mime_type = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "bmp": "bmp"}.get(ext, "jpeg")

    try:
        response = httpx.post(
            f"{VISION_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {VISION_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": VISION_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{mime_type};base64,{base64_image}"
                                },
                            },
                            {
                                "type": "text",
                                "text": "请详细描述这张图片的内容，包括文字、图表、场景等所有可见信息。用中文回答。",
                            },
                        ],
                    }
                ],
                "max_tokens": 1024,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        result = response.json()
        description = result["choices"][0]["message"]["content"]

        logger.info(f"视觉模型描述完成: {len(description)} 个字符")
        return description

    except Exception as e:
        logger.error(f"视觉模型调用失败: {e}")
        return f"（视觉模型调用失败: {e}）"


def process_image(image_path: str) -> str:
    """
    双通道处理图片：OCR 提取文字 + 视觉模型生成描述

    Args:
        image_path: 图片文件路径

    Returns:
        合并后的文本内容
    """
    # 通道 1: OCR 提取文字
    ocr_text = extract_text_ocr(image_path)

    # 通道 2: 视觉模型描述
    vlm_description = describe_image_vlm(image_path)

    # 合并结果
    parts = []
    if ocr_text.strip():
        parts.append(f"【OCR 提取文字】\n{ocr_text}")
    if vlm_description.strip() and "未配置" not in vlm_description and "失败" not in vlm_description:
        parts.append(f"【图片内容描述】\n{vlm_description}")

    if not parts:
        return "（未能从图片中提取任何内容）"

    return "\n\n".join(parts)
