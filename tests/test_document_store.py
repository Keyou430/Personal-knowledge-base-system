from core.document_store import ChunkRecord, DocumentStore


def make_chunk(chunk_id: str, content: str, section_title: str = "") -> ChunkRecord:
    return ChunkRecord(id=chunk_id, content=content, section_title=section_title)


def test_document_store_persists_active_document_and_fts_results(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    document = store.create_document(
        domain="制度",
        name="报销制度.md",
        category="制度",
        owner="财务部",
        source="upload",
        content="报销流程需要提交发票和审批单。",
        chunks=[make_chunk("chunk-1", "报销流程需要提交发票和审批单。", "报销流程")],
    )

    assert document.status == "active"
    assert document.version == "1"
    assert store.search_keyword("报销")[0].id == "chunk-1"
    assert store.get_chunks(document.id)[0].section_title == "报销流程"


def test_same_hash_is_idempotent_and_changed_content_supersedes_old_version(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    first = store.create_document(
        domain="制度",
        name="制度.md",
        category="制度",
        owner="",
        source="upload",
        content="第一版内容",
        chunks=[make_chunk("one", "第一版内容")],
    )
    same = store.create_document(
        domain="制度",
        name="制度.md",
        category="制度",
        owner="",
        source="upload",
        content="第一版内容",
        chunks=[make_chunk("ignored", "第一版内容")],
    )
    assert same.id == first.id

    second = store.replace_document(
        first.id,
        domain="制度",
        name="制度.md",
        category="制度",
        owner="",
        source="upload",
        content="第二版内容",
        chunks=[make_chunk("two", "第二版内容")],
        version="2",
    )
    assert second.version == "2"
    assert store.get(first.id).status == "superseded"
    assert store.search_keyword("第一版") == []
    assert store.search_keyword("第二版")[0].id == "two"


def test_chunk_write_rolls_back_document_when_insert_fails(tmp_path):
    store = DocumentStore(tmp_path / "documents.db")
    bad_chunks = [make_chunk("same", "a"), make_chunk("same", "b")]

    try:
        store.create_document(
            domain="制度",
            name="坏文件.md",
            category="制度",
            owner="",
            source="upload",
            content="ab",
            chunks=bad_chunks,
        )
    except Exception:
        pass

    assert store.list_documents() == []
