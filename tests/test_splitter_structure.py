from langchain_core.documents import Document

from core.splitter import split_documents


def test_splitter_carries_heading_context_and_keeps_table_atomic():
    docs = [
        Document(page_content="标题下的制度说明。", metadata={"block_type": "heading", "heading_path": "制度"}),
        Document(page_content="表头 | 内容\n角色 | 审批人", metadata={"block_type": "table", "heading_path": "制度", "table_index": 0}),
    ]

    chunks = split_documents(docs, chunk_size=30, chunk_overlap=0)

    table = next(chunk for chunk in chunks if chunk.metadata["block_type"] == "table")
    assert table.page_content.count("审批人") == 1
    assert table.metadata["heading_path"] == "制度"


def test_splitter_keeps_faq_question_with_answer():
    document = Document(
        page_content="问题：如何报销？\n答案：提交发票和审批单。\n\n问题：多久到账？\n答案：三个工作日。",
        metadata={"source": "faq.md"},
    )

    chunks = split_documents([document], chunk_size=40, chunk_overlap=0)

    assert len(chunks) == 2
    assert "如何报销" in chunks[0].page_content
    assert "提交发票" in chunks[0].page_content
    assert "多久到账" in chunks[1].page_content


def test_splitter_preserves_pdf_page_metadata():
    document = Document(page_content="这是第 3 页的内容。" * 20, metadata={"source": "guide.pdf", "page": 2})

    chunks = split_documents([document], chunk_size=30, chunk_overlap=0)

    assert chunks
    assert all(chunk.metadata["page"] == 2 for chunk in chunks)
