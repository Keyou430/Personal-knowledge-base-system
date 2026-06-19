# -*- coding: utf-8 -*-
"""
Embedding 模型封装
使用 BGE-small-zh-v1.5，首次运行自动下载，之后从缓存加载
"""

import logging

from langchain_huggingface import HuggingFaceEmbeddings

from config import EMBEDDING_MODEL_NAME, EMBEDDING_DEVICE

logger = logging.getLogger(__name__)

# 模块级缓存，避免重复加载模型
_embedding_instance = None


def get_embedding_model() -> HuggingFaceEmbeddings:
    """
    获取 Embedding 模型实例（单例模式）

    首次调用时加载模型（约 90MB），后续调用直接返回缓存实例。
    通过 HF_ENDPOINT 环境变量使用国内镜像下载。

    Returns:
        HuggingFaceEmbeddings 实例
    """
    global _embedding_instance

    if _embedding_instance is None:
        logger.info(f"正在加载 Embedding 模型: {EMBEDDING_MODEL_NAME}")
        logger.info(f"运行设备: {EMBEDDING_DEVICE}")

        _embedding_instance = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": EMBEDDING_DEVICE},
            encode_kwargs={"normalize_embeddings": True},  # BGE 模型需要归一化
        )

        logger.info("Embedding 模型加载完成")

    return _embedding_instance
