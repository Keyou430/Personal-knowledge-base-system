# -*- coding: utf-8 -*-
"""
文本切分器
使用 LangChain RecursiveCharacterTextSplitter，中文分隔符优先
"""

import logging
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import CHUNK_SIZE, CHUNK_OVERLAP

logger = logging.getLogger(__name__)

# 中文优化的分隔符列表
# 优先按段落分割，其次按句子，最后按空格
CHINESE_SEPARATORS = [
    "\n\n",     # 段落分隔
    "\n",       # 换行
    "。",       # 中文句号
    "！",       # 中文感叹号
    "？",       # 中文问号
    "；",       # 中文分号
    "，",       # 中文逗号
    " ",        # 空格
    ".",        # 英文句号
    "!",        # 英文感叹号
    "?",        # 英文问号
]


def create_splitter(
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> RecursiveCharacterTextSplitter:
    """
    创建文本切分器实例

    Args:
        chunk_size: 每个文本块的最大字符数
        chunk_overlap: 相邻文本块的重叠字符数

    Returns:
        配置好的 TextSplitter 实例
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=CHINESE_SEPARATORS,
        length_function=len,
    )


def split_documents(
    documents: List[Document],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[Document]:
    """
    将文档列表切分为较小的文本块

    Args:
        documents: 原始文档列表
        chunk_size: 每个文本块的最大字符数
        chunk_overlap: 相邻文本块的重叠字符数

    Returns:
        切分后的 Document 列表，保留原始元数据
    """
    if not documents:
        logger.warning("输入文档列表为空")
        return []

    splitter = create_splitter(chunk_size, chunk_overlap)
    chunks = splitter.split_documents(documents)

    # 为每个切片添加序号元数据
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = i
        chunk.metadata["total_chunks"] = len(chunks)

    logger.info(
        f"文本切分完成: {len(documents)} 个文档 -> {len(chunks)} 个文本块 "
        f"(chunk_size={chunk_size}, overlap={chunk_overlap})"
    )
    return chunks
