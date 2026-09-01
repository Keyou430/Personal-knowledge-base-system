from langchain_core.documents import Document

from core.document_store import ChunkRecord, DocumentStore
from core.hybrid_retriever import HybridRetriever


def seed_store(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    doc = store.create_document(
        domain="制度",
        name="报销制度.md",
        category="制度",
        owner="",
        source="upload",
        content="报销编号 BX-2026-01，生效日期 2026-01-01。",
        chunks=[
            ChunkRecord(
                id="chunk-keyword",
                content="报销编号 BX-2026-01，生效日期 2026-01-01。",
                section_title="编号与日期",
            ),
            ChunkRecord(id="chunk-other", content="员工福利说明。"),
        ],
    )
    return store, doc


def test_hybrid_retriever_merges_keyword_and_semantic_scores(tmp_path):
    store, doc = seed_store(tmp_path)

    def semantic_search(query, domain, top_k):
        return [
            (
                Document(
                    page_content="报销编号 BX-2026-01，生效日期 2026-01-01。",
                    metadata={"chunk_id": "chunk-keyword", "document_id": doc.id, "document_version": doc.version},
                ),
                0.1,
            )
        ]

    results = HybridRetriever(store, semantic_search, min_score=0.1).search(
        "BX-2026-01", "制度"
    )

    assert results[0].id == "chunk-keyword"
    assert results[0].keyword_score > 0
    assert results[0].semantic_score > 0
    assert results[0].score > 0


def test_hybrid_retriever_filters_inactive_semantic_versions_and_low_scores(tmp_path):
    store, doc = seed_store(tmp_path)
    old = store.replace_document(
        doc.id,
        domain="制度",
        name="报销制度.md",
        category="制度",
        owner="",
        source="upload",
        content="旧编号 OLD-1。",
        chunks=[ChunkRecord(id="old-chunk", content="旧编号 OLD-1。")],
        version="2",
    )

    def semantic_search(query, domain, top_k):
        return [
            (
                Document(
                    page_content="报销编号 BX-2026-01。",
                    metadata={"chunk_id": "chunk-keyword", "document_id": doc.id, "document_version": "1"},
                ),
                0.0,
            )
        ]

    assert HybridRetriever(store, semantic_search, min_score=0.99).search("无关", "制度") == []
    assert HybridRetriever(store, semantic_search, min_score=0.0).search("BX-2026-01", "制度") == []


def test_hybrid_retriever_filters_semantic_results_with_missing_source(tmp_path):
    store, doc = seed_store(tmp_path)
    store.mark_source_present(doc.id, False)

    def semantic_search(query, domain, top_k):
        return [
            Document(
                page_content="报销编号 BX-2026-01。",
                metadata={"chunk_id": "chunk-keyword", "document_id": doc.id, "document_version": doc.version},
            )
        ]

    assert HybridRetriever(store, semantic_search, min_score=0.0).search("BX-2026-01", "制度") == []
