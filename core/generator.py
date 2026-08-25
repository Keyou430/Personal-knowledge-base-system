# -*- coding: utf-8 -*-
"""
RAG 问答生成器
使用 OpenAI 兼容接口调用 LLM，基于检索到的文档生成回答
"""

import logging
from typing import Any, List

from openai import OpenAI
from langchain_core.documents import Document

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = "知识库暂无相关内容，请补充或更新相关文档"

# 系统提示词
SYSTEM_PROMPT = """你是一个知识库助手。你的任务是基于提供的参考资料回答用户问题。

规则：
1. 只根据参考资料回答，不要编造信息
2. 如果参考资料中没有相关信息，明确告知用户
3. 先给出简明结论，再给出资料中的依据
4. 回答末尾必须列出引用，包含文档名称、版本、章节或页码和片段摘要
5. 回答要准确、简洁、有条理"""

# 引用格式提示
CITATION_PROMPT = """
请在回答末尾添加"📚 引用来源"部分，列出你参考的文档，格式如下：
📚 引用来源：
1. [文档路径] — 相关内容摘要
"""


def build_context(documents: List[Any]) -> str:
    """
    将检索到的文档构建为上下文文本

    Args:
        documents: 检索到的文档列表

    Returns:
        格式化的上下文文本
    """
    if not documents:
        return "（未找到相关参考资料）"

    context_parts = []
    for i, doc in enumerate(documents, 1):
        if hasattr(doc, "document_name"):
            source = doc.document_name
            version = str(doc.document_version or "未知")
            section = doc.section_title or "未标注"
            page = doc.page
            content = doc.content.strip()
            keyword_score = getattr(doc, "keyword_score", 0.0)
            semantic_score = getattr(doc, "semantic_score", 0.0)
        else:
            metadata = getattr(doc, "metadata", {})
            source = metadata.get("document_name") or metadata.get("source", "未知来源")
            version = str(metadata.get("document_version", "未知"))
            section = metadata.get("section_title") or metadata.get("heading_path", "未标注")
            page = metadata.get("page")
            content = doc.page_content.strip()
            keyword_score = metadata.get("keyword_score", 0.0)
            semantic_score = metadata.get("semantic_score", 0.0)
        page_text = f"页码: {page}" if page is not None else "页码: 未标注"
        context_parts.append(
            f"【参考资料 {i}】文档: {source}；版本: {version}；章节: {section}；{page_text}\n"
            f"召回贡献: 关键词 {keyword_score:.2f} / 语义 {semantic_score:.2f}\n{content}"
        )

    return "\n\n".join(context_parts)


def _build_messages(question: str, documents: List[Document]) -> list:
    """构建 LLM 消息列表（共享逻辑）"""
    context = build_context(documents)
    user_message = f"""参考资料：
{context}

用户问题：{question}

{CITATION_PROMPT}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def _check_api_key() -> str | None:
    """检查 API Key，返回错误信息或 None"""
    if not LLM_API_KEY:
        return "❌ 错误：未配置 LLM API Key，请在 .env 文件中设置 LLM_API_KEY"
    return None


def generate_answer(
    question: str,
    documents: List[Document],
    model: str = LLM_MODEL,
    temperature: float = 0.3,
) -> str:
    """
    基于检索到的文档生成回答

    Args:
        question: 用户问题
        documents: 检索到的相关文档列表
        model: LLM 模型名称
        temperature: 生成温度（越低越确定）

    Returns:
        生成的回答文本
    """
    if not documents:
        return REFUSAL_MESSAGE

    error = _check_api_key()
    if error:
        return error

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        messages = _build_messages(question, documents)

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
        )

        if not response.choices:
            return "❌ LLM 返回了空响应，请重试"

        answer = response.choices[0].message.content
        logger.info(f"生成回答完成，长度: {len(answer)} 字符")
        return answer

    except Exception as e:
        error_msg = f"调用 LLM API 失败: {e}"
        logger.error(error_msg)
        return f"❌ {error_msg}"


def generate_answer_stream(
    question: str,
    documents: List[Document],
    model: str = LLM_MODEL,
    temperature: float = 0.3,
):
    """
    流式生成回答（用于 Streamlit 实时显示）

    Args:
        question: 用户问题
        documents: 检索到的相关文档列表
        model: LLM 模型名称
        temperature: 生成温度

    Yields:
        生成的文本片段
    """
    if not documents:
        yield REFUSAL_MESSAGE
        return

    error = _check_api_key()
    if error:
        yield error
        return

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        messages = _build_messages(question, documents)

        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
            stream=True,
        )

        for chunk in stream:
            # 防御：choices 可能为空列表（API 错误 chunk）
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except Exception as e:
        error_msg = f"调用 LLM API 失败: {e}"
        logger.error(error_msg)
        yield f"❌ {error_msg}"
