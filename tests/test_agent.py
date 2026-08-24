import json
from types import SimpleNamespace

import pytest

import core.agent as agent
from core.agent import AgentError, AgentOutputError, draft_experience, parse_experience_draft


def test_parse_experience_draft_requires_all_card_fields():
    result = parse_experience_draft(
        '{"title":"标题","scenario":"场景","conclusion":"结论",'
        '"steps":["一步"],"tags":["标签"],"sources":[]}'
    )

    assert result.title == "标题"
    assert result.steps == ["一步"]


def test_parse_experience_draft_rejects_missing_conclusion():
    with pytest.raises(AgentOutputError, match="conclusion"):
        parse_experience_draft(
            '{"title":"标题","scenario":"场景","steps":[],"tags":[],"sources":[]}'
        )


def test_parse_experience_draft_accepts_fenced_json():
    result = parse_experience_draft(
        '```json\n{"title":"标题","scenario":"场景","conclusion":"结论",'
        '"steps":[],"tags":[],"sources":[]}\n```'
    )
    assert result.title == "标题"


def test_draft_experience_sends_only_current_question_answer_and_sources():
    class FakeCompletions:
        def __init__(self):
            self.request = None

        def create(self, **kwargs):
            self.request = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"title":"标题","scenario":"场景","conclusion":"结论",'
                            '"steps":["一步"],"tags":[],"sources":[]}'
                        )
                    )
                ]
            )

    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    sources = [{"source": "notes.md", "excerpt": "引用内容", "page": 2}]

    draft = draft_experience("当前问题", "当前回答", sources, client=client)

    payload = json.loads(completions.request["messages"][1]["content"])
    assert draft.question == "当前问题"
    assert payload == {
        "question": "当前问题",
        "answer": "当前回答",
        "sources": sources,
    }


def test_draft_experience_requires_configured_credential(monkeypatch):
    monkeypatch.setattr(agent, "LLM_API_KEY", "")

    with pytest.raises(AgentError, match="未配置"):
        draft_experience("问题", "回答", [], client=None)
