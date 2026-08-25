from core import generator
from core.hybrid_retriever import RetrievedChunk


def test_context_contains_auditable_document_version_section_and_page():
    chunk = RetrievedChunk(
        id="chunk-1",
        document_id="doc-1",
        document_version="2",
        document_name="报销制度.md",
        category="制度",
        content="提交发票和审批单。",
        section_title="报销流程",
        page=3,
        score=0.8,
        keyword_score=0.7,
        semantic_score=0.9,
    )

    context = generator.build_context([chunk])

    assert "报销制度.md" in context
    assert "版本: 2" in context
    assert "报销流程" in context
    assert "页码: 3" in context
    assert "提交发票" in context


def test_empty_retrieval_refuses_without_constructing_client(monkeypatch):
    monkeypatch.setattr(generator, "LLM_API_KEY", "")

    def fail_client(*args, **kwargs):
        raise AssertionError("client must not be created")

    monkeypatch.setattr(generator, "OpenAI", fail_client)
    assert generator.generate_answer("无关问题", []) == generator.REFUSAL_MESSAGE
    assert list(generator.generate_answer_stream("无关问题", [])) == [generator.REFUSAL_MESSAGE]


def test_prompt_requires_conclusion_evidence_and_citations():
    messages = generator._build_messages("问题", [])
    system = messages[0]["content"]
    assert "结论" in system
    assert "依据" in system
    assert "引用" in system
