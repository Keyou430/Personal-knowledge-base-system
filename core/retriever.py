# -*- coding: utf-8 -*-
"""
向量存储与语义检索
基于 Chroma 数据库，支持多领域分库管理
"""

import os
import re
import logging
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_chroma import Chroma

from config import DOMAINS_DIR, RETRIEVAL_TOP_K
from core.embedder import get_embedding_model

logger = logging.getLogger(__name__)

# 领域名 -> Chroma 实例的缓存，避免重复创建
_vectorstore_cache: dict[str, Chroma] = {}


def _sanitize_collection_name(domain: str) -> str:
    """
    将领域名转换为合法的 Chroma collection 名称
    Chroma 要求 collection 名称: 3-63字符，以字母数字开头结尾，只含 [a-zA-Z0-9._-]
    """
    import hashlib
    # 将中文和特殊字符替换为下划线
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
    # 确保以字母数字开头
    sanitized = re.sub(r"^[^a-zA-Z0-9]+", "", sanitized)
    # 确保以字母数字结尾
    sanitized = re.sub(r"[^a-zA-Z0-9]+$", "", sanitized)
    # 压缩连续下划线
    sanitized = re.sub(r"_+", "_", sanitized)
    # 截断到 63 字符
    sanitized = sanitized[:63]
    # 保底：纯中文/特殊字符域名会变成空串，用 hash 生成合法名称
    if len(sanitized) < 3:
        h = hashlib.md5(domain.encode("utf-8")).hexdigest()[:8]
        sanitized = f"kb_{h}"
    return sanitized


def _get_domain_path(domain: str) -> str:
    """获取领域的 ChromaDB 存储路径"""
    return os.path.join(DOMAINS_DIR, domain, "chroma_db")


def get_vectorstore(domain: str = "默认") -> Chroma:
    """
    获取指定领域的向量数据库实例（带缓存）

    Args:
        domain: 领域名称，默认为"默认"

    Returns:
        Chroma 向量数据库实例
    """
    if domain in _vectorstore_cache:
        return _vectorstore_cache[domain]

    persist_directory = _get_domain_path(domain)
    os.makedirs(persist_directory, exist_ok=True)

    embedding = get_embedding_model()
    collection_name = _sanitize_collection_name(domain)

    vectorstore = Chroma(
        persist_directory=persist_directory,
        embedding_function=embedding,
        collection_name=collection_name,
    )

    _vectorstore_cache[domain] = vectorstore
    logger.info(f"已加载领域向量库: {domain} (collection={collection_name}, path={persist_directory})")
    return vectorstore


def add_documents(
    documents: List[Document],
    domain: str = "默认",
) -> int:
    """
    将文档添加到指定领域的向量库

    Args:
        documents: 切分后的文档列表
        domain: 领域名称

    Returns:
        添加的文档数量
    """
    if not documents:
        logger.warning("没有文档需要添加")
        return 0

    vectorstore = get_vectorstore(domain)
    vectorstore.add_documents(documents)

    logger.info(f"已向领域 [{domain}] 添加 {len(documents)} 个文档片段")
    return len(documents)


def search(
    query: str,
    domain: str = "默认",
    top_k: int = RETRIEVAL_TOP_K,
) -> List[Document]:
    """
    语义搜索

    Args:
        query: 查询文本
        domain: 领域名称
        top_k: 返回结果数量

    Returns:
        相关文档列表
    """
    vectorstore = get_vectorstore(domain)
    results = vectorstore.similarity_search(query, k=top_k)

    logger.info(f"语义搜索完成: query='{query[:30]}...', 返回 {len(results)} 条结果")
    return results


def search_with_score(
    query: str,
    domain: str = "默认",
    top_k: int = RETRIEVAL_TOP_K,
) -> List[Tuple[Document, float]]:
    """
    带相似度分数的语义搜索

    Args:
        query: 查询文本
        domain: 领域名称
        top_k: 返回结果数量

    Returns:
        (文档, 相似度分数) 元组列表，分数越低越相似
    """
    vectorstore = get_vectorstore(domain)
    results = vectorstore.similarity_search_with_score(query, k=top_k)

    logger.info(f"带分数搜索完成: query='{query[:30]}...', 返回 {len(results)} 条结果")
    return results


def list_domains() -> List[str]:
    """
    列出所有已创建的领域

    Returns:
        领域名称列表
    """
    if not os.path.exists(DOMAINS_DIR):
        return []

    domains = []
    for name in os.listdir(DOMAINS_DIR):
        domain_path = os.path.join(DOMAINS_DIR, name)
        if os.path.isdir(domain_path):
            domains.append(name)

    return sorted(domains)


def create_domain(domain: str) -> bool:
    """
    创建新领域

    Args:
        domain: 领域名称

    Returns:
        是否创建成功
    """
    domain_path = _get_domain_path(domain)

    if os.path.exists(domain_path):
        logger.warning(f"领域已存在: {domain}")
        return False

    os.makedirs(domain_path, exist_ok=True)
    # 初始化空的向量库
    get_vectorstore(domain)

    logger.info(f"已创建领域: {domain}")
    return True


def delete_domain(domain: str) -> bool:
    """
    删除领域及其向量库数据

    Args:
        domain: 领域名称

    Returns:
        是否删除成功
    """
    import shutil

    domain_path = os.path.join(DOMAINS_DIR, domain)

    if not os.path.exists(domain_path):
        logger.warning(f"领域不存在: {domain}")
        return False

    # 先清除缓存中的实例引用，避免持有已删除目录的句柄
    _vectorstore_cache.pop(domain, None)

    shutil.rmtree(domain_path)
    logger.info(f"已删除领域: {domain}")
    return True


def list_domain_files(domain: str = "默认") -> List[dict]:
    """
    列出指定领域下的所有原始文档文件

    Args:
        domain: 领域名称

    Returns:
        文件信息字典列表，包含 name, size, path 等
    """
    from config import RAW_DIR

    domain_raw_dir = os.path.join(RAW_DIR, domain)
    if not os.path.exists(domain_raw_dir):
        return []

    files = []
    for name in sorted(os.listdir(domain_raw_dir)):
        file_path = os.path.join(domain_raw_dir, name)
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            # 格式化文件大小
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"

            ext = os.path.splitext(name)[1].lower()
            # 文件类型图标
            icon_map = {
                ".pdf": "📄", ".docx": "📝", ".doc": "📝",
                ".pptx": "📊", ".ppt": "📊", ".md": "📋",
                ".txt": "📃", ".jpg": "🖼️", ".jpeg": "🖼️",
                ".png": "🖼️", ".bmp": "🖼️",
            }
            icon = icon_map.get(ext, "📎")

            files.append({
                "name": name,
                "icon": icon,
                "size": size_str,
                "ext": ext,
                "path": file_path,
            })

    return files


def get_domain_stats(domain: str = "默认") -> dict:
    """
    获取领域的统计信息

    Args:
        domain: 领域名称

    Returns:
        包含文档数量等统计信息的字典
    """
    try:
        vectorstore = get_vectorstore(domain)
        collection = vectorstore._collection
        count = collection.count()
        return {
            "domain": domain,
            "document_count": count,
        }
    except Exception as e:
        logger.error(f"获取领域统计失败: {e}")
        return {"domain": domain, "document_count": 0}
