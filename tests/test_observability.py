import sqlite3

from core.document_store import ChunkRecord, DocumentStore
from core.observability import (
    QueryTraceStore,
    document_status_label,
    get_index_health,
    record_query_trace_safely,
)


def test_query_trace_is_privacy_safe_and_records_versions(tmp_path):
    store = QueryTraceStore(tmp_path / "observability.db")
    assert record_query_trace_safely(
        store,
        domain="制度",
        retrieval_count=2,
        selected_versions=[("C:/private/customer.md", "2"), ("制度.md", "2")],
        refusal_reason=None,
    )

    traces = store.list_recent(limit=10)
    assert len(traces) == 1
    assert traces[0].domain == "制度"
    assert traces[0].retrieval_count == 2
    assert traces[0].selected_versions == ("customer.md@2", "制度.md@2")
    assert traces[0].refusal_reason is None

    with sqlite3.connect(tmp_path / "observability.db") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(query_traces)")}
    assert {"question", "answer", "content", "chunk_text"}.isdisjoint(columns)


def test_trace_write_failure_never_raises():
    class BrokenStore:
        def record(self, **kwargs):
            raise OSError("trace unavailable")

    assert record_query_trace_safely(
        BrokenStore(),
        domain="制度",
        retrieval_count=0,
        selected_versions=[],
        refusal_reason="no_relevant_sources",
    ) is False


def test_index_health_distinguishes_active_superseded_failed_and_pending(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    active = store.create_document(
        domain="制度", name="active.md", category="制度", owner="", source="upload",
        content="active", chunks=[ChunkRecord(id="a", content="active")], version="2",
    )
    superseded = store.create_document(
        domain="制度", name="history.md", category="制度", owner="", source="upload",
        content="old", chunks=[ChunkRecord(id="h1", content="old")], version="1",
    )
    store.replace_document(
        superseded.id,
        domain="制度", name="history.md", category="制度", owner="", source="upload",
        content="new", chunks=[ChunkRecord(id="h2", content="new")], version="2",
    )
    failed = store.create_document(
        domain="制度", name="failed.md", category="制度", owner="", source="upload",
        content="failed", chunks=[ChunkRecord(id="f", content="failed")], version="1",
    )
    store.mark_index_pending(active.id, True)
    with store._connect() as connection:
        connection.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (failed.id,))

    health = get_index_health(store, "制度")
    assert health["active"] == 2
    assert health["superseded"] == 1
    assert health["failed"] == 1
    assert health["index_pending"] == 1
    assert health["status"] == "attention"
    assert document_status_label("active", False) == "当前生效"
    assert document_status_label("superseded", False) == "历史版本"
    assert document_status_label("failed", False) == "处理失败"
    assert document_status_label("active", True) == "待重建索引"
