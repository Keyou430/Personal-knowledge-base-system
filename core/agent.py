# -*- coding: utf-8 -*-
"""Cloud-assisted, validated drafting for experience cards."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from core.experience_store import ExperienceDraft


class AgentError(RuntimeError):
    """A user-actionable error while drafting an experience card."""


class AgentOutputError(AgentError):
    """The cloud response cannot safely become an editable card."""


_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_MAX_LIST_ITEMS = 20
_MAX_ITEM_LENGTH = 500

_SYSTEM_PROMPT = """你是个人知识库的经验整理助手。
仅根据用户提供的问题、回答和引用资料，生成一张可编辑的经验卡片。
不要编造事实，也不要输出 Markdown 或解释文字。必须返回 JSON 对象，包含：
title、scenario、conclusion、steps、tags、sources。
steps、tags、sources 必须是数组。sources 中每个对象必须包含 source 和 excerpt。"""


def parse_experience_draft(
    response_text: str,
    *,
    question: str = "",
    answer_excerpt: str = "",
) -> ExperienceDraft:
    """Parse and validate a model response before presenting it to a user."""
    text = response_text.strip()
    match = _JSON_FENCE.match(text)
    if match:
        text = match.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise AgentOutputError("模型未返回可解析的 JSON 草稿") from error

    if not isinstance(payload, dict):
        raise AgentOutputError("模型返回的经验草稿必须是对象")

    title = _required_text(payload, "title")
    scenario = _required_text(payload, "scenario")
    conclusion = _required_text(payload, "conclusion")
    steps = _string_list(payload, "steps")
    tags = _string_list(payload, "tags")
    sources = _source_list(payload, "sources")

    return ExperienceDraft(
        title=title,
        scenario=scenario,
        conclusion=conclusion,
        steps=steps,
        tags=tags,
        sources=sources,
        question=question,
        answer_excerpt=answer_excerpt,
    )


def draft_experience(
    question: str,
    answer: str,
    sources: list[dict[str, Any]],
    *,
    client: Any | None = None,
) -> ExperienceDraft:
    """Draft one card using only an explicitly supplied question-answer record."""
    if not LLM_API_KEY and client is None:
        raise AgentError("未配置 LLM API Key，无法生成经验草稿")

    normalized_sources = _source_list({"sources": sources}, "sources")
    payload = {
        "question": question.strip(),
        "answer": answer.strip(),
        "sources": normalized_sources,
    }
    active_client = client or OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        timeout=45.0,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = active_client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=1200,
            )
            choices = getattr(response, "choices", None)
            content = choices[0].message.content if choices else None
            if not content:
                raise AgentOutputError("模型返回了空经验草稿")
            return parse_experience_draft(
                content,
                question=question,
                answer_excerpt=answer[:1000],
            )
        except AgentOutputError:
            raise
        except Exception as error:
            last_error = error
            if attempt == 1:
                break

    raise AgentError("生成经验草稿失败，请稍后重试") from last_error


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgentOutputError(f"经验草稿缺少有效字段: {field}")
    return value.strip()[:_MAX_ITEM_LENGTH]


def _string_list(payload: dict[str, Any], field: str) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise AgentOutputError(f"经验草稿字段必须是数组: {field}")
    result = []
    for item in value[:_MAX_LIST_ITEMS]:
        if not isinstance(item, str):
            raise AgentOutputError(f"经验草稿数组包含非文本值: {field}")
        normalized = item.strip()
        if normalized:
            result.append(normalized[:_MAX_ITEM_LENGTH])
    return result


def _source_list(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise AgentOutputError(f"经验草稿字段必须是数组: {field}")

    result: list[dict[str, Any]] = []
    for item in value[:_MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            raise AgentOutputError("经验草稿 sources 必须包含对象")
        source = item.get("source")
        excerpt = item.get("excerpt")
        if not isinstance(source, str) or not source.strip():
            raise AgentOutputError("经验草稿来源缺少 source")
        if not isinstance(excerpt, str):
            raise AgentOutputError("经验草稿来源缺少 excerpt")
        result.append(
            {
                **item,
                "source": source.strip()[:_MAX_ITEM_LENGTH],
                "excerpt": excerpt.strip()[:_MAX_ITEM_LENGTH],
            }
        )
    return result
