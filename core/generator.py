# -*- coding: utf-8 -*-
"""
RAG 问答生成器
使用 OpenAI 兼容接口调用 LLM，基于检索到的文档生成回答
"""

import logging
from typing import List

from openai import OpenAI
from langchain_core.documents import Document

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

# 系统提示词
SYSTEM_PROMPT = """你是一个知识库助手。你的任务是基于提供的参考资料回答用户问题。

规则：
1. 只根据参考资料回答，不要编造信息
2. 如果参考资料中没有相关信息，明确告知用户
3. 回答末尾标注引用来源（文档路径和相关内容摘要）
4. 回答要准确、简洁、有条理"""

# 引用格式提示
CITATION_PROMPT = """
请在回答末尾添加"📚 引用来源"部分，列出你参考的文档，格式如下：
📚 引用来源：
1. [文档路径] — 相关内容摘要
"""


def build_context(documents: List[Document]) -> str:
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
        source = doc.metadata.get("source", "未知来源")
        content = doc.page_content.strip()
        context_parts.append(f"【参考资料 {i}】来源: {source}\n{content}")

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
