# -*- coding: utf-8 -*-
"""
文本切分器
使用 LangChain RecursiveCharacterTextSplitter，中文分隔符优先
"""

import logging
import re
from typing import List

from langchain_core.documents import Document

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
) -> object:
    """
    创建文本切分器实例

    Args:
        chunk_size: 每个文本块的最大字符数
        chunk_overlap: 相邻文本块的重叠字符数

    Returns:
        配置好的 TextSplitter 实例
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

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

    chunks: List[Document] = []
    structured = any(doc.metadata.get("block_type") for doc in documents)

    for document in documents:
        metadata = dict(document.metadata)
        block_type = metadata.get("block_type", "paragraph")
        if block_type == "heading":
            chunks.append(Document(page_content=document.page_content.strip(), metadata=metadata))
            continue
        if not document.page_content.strip():
            continue

        if block_type == "table":
            chunks.extend(_split_table(document, chunk_size))
            continue

        faq_parts = _split_faq(document.page_content)
        if len(faq_parts) > 1:
            for part in faq_parts:
                part_metadata = dict(metadata)
                part_metadata["block_type"] = "faq"
                chunks.extend(_split_plain(part, part_metadata, chunk_size, chunk_overlap))
        else:
            chunks.extend(_split_plain(document.page_content, metadata, chunk_size, chunk_overlap))

    # 为每个切片添加序号元数据
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = i
        chunk.metadata["total_chunks"] = len(chunks)

    logger.info(
        f"文本切分完成: {len(documents)} 个文档 -> {len(chunks)} 个文本块 "
        f"(chunk_size={chunk_size}, overlap={chunk_overlap})"
    )
    return chunks


def _split_plain(
    content: str,
    metadata: dict,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Document]:
    text = content.strip()
    if len(text) <= chunk_size:
        return [Document(page_content=text, metadata=dict(metadata))]

    # Keep paragraph and sentence boundaries first, then use bounded slicing.
    units = [unit.strip() for unit in re.split(r"\n\s*\n|(?<=[。！？!?；;])", text) if unit.strip()]
    pieces: list[str] = []
    current = ""
    for unit in units or [text]:
        if len(unit) > chunk_size:
            if current:
                pieces.append(current)
                current = ""
            start = 0
            step = max(1, chunk_size - min(chunk_overlap, chunk_size - 1))
            while start < len(unit):
                pieces.append(unit[start : start + chunk_size])
                start += step
            continue
        candidate = f"{current}{unit}" if not current else f"{current}{unit}"
        if current and len(candidate) > chunk_size:
            pieces.append(current)
            overlap = current[-chunk_overlap:] if chunk_overlap else ""
            current = f"{overlap}{unit}" if overlap else unit
        else:
            current = candidate
    if current:
        pieces.append(current)
    return [Document(page_content=piece, metadata=dict(metadata)) for piece in pieces if piece.strip()]


def _split_table(document: Document, chunk_size: int) -> List[Document]:
    """Keep tables whole when possible; overflow is grouped by complete rows."""
    text = document.page_content.strip()
    if len(text) <= chunk_size:
        return [Document(page_content=text, metadata=dict(document.metadata))]
    rows = [row for row in text.splitlines() if row.strip()]
    result: List[Document] = []
    current: list[str] = []
    current_length = 0
    for row in rows:
        if current and current_length + len(row) + 1 > chunk_size:
            result.append(Document("\n".join(current), dict(document.metadata)))
            current = []
            current_length = 0
        current.append(row)
        current_length += len(row) + 1
    if current:
        result.append(Document("\n".join(current), dict(document.metadata)))
    return result


def _split_faq(content: str) -> List[str]:
    """Split common Chinese/English FAQ markers without separating answers."""
    matches = list(re.finditer(r"(?m)^(?:问题|问|Q\s*\d*)\s*[:：]?", content))
    if len(matches) < 2:
        return [content.strip()]
    parts = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        value = content[match.start() : end].strip()
        if value:
            parts.append(value)
    return parts
