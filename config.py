# -*- coding: utf-8 -*-
"""
全局配置文件
定义系统运行所需的所有参数
"""

import os
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

# ============================================================
# Embedding 模型配置
# ============================================================
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"  # 中文优化的小型 Embedding 模型
EMBEDDING_DEVICE = "cpu"                          # 运行设备（cpu / cuda）

# ============================================================
# 文本切分配置
# ============================================================
CHUNK_SIZE = 500       # 每个文本块的最大字符数
CHUNK_OVERLAP = 100    # 相邻文本块之间的重叠字符数

# ============================================================
# 检索配置
# ============================================================
RETRIEVAL_TOP_K = 5    # 语义检索返回的最相关文档数量

# ============================================================
# LLM 配置（硅基流动 API）
# ============================================================
LLM_MODEL = os.getenv("LLM_MODEL", "XiaomiMiMo/MiMo-7B-RL")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# ============================================================
# 视觉模型配置（Qwen2.5-VL）
# ============================================================
VISION_MODEL = os.getenv("VISION_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
VISION_API_KEY = os.getenv("VISION_API_KEY", LLM_API_KEY)
VISION_BASE_URL = os.getenv("VISION_BASE_URL", LLM_BASE_URL)

# ============================================================
# HuggingFace 镜像配置（国内加速下载模型）
# ============================================================
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["HF_ENDPOINT"] = HF_ENDPOINT

# ============================================================
# 数据存储路径
# ============================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")                # 原始文档存放
DOMAINS_DIR = os.path.join(DATA_DIR, "domains")        # 多领域知识库根目录

# 支持的文档格式
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".md", ".txt", ".jpg", ".jpeg", ".png", ".bmp",
}
