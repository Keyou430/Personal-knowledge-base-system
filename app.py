# -*- coding: utf-8 -*-
"""
个人知识库系统 — Streamlit 主入口
支持多领域管理、多格式文档上传、RAG 智能问答
"""

import os
import re
import sys
import logging
import tempfile
import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional

import streamlit as st

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BACKUP_DIR,
    DOCUMENT_DB_PATH,
    EXPERIENCE_DB_PATH,
    OBSERVABILITY_DB_PATH,
    RAW_DIR,
    RETRIEVAL_TOP_K,
)
from core.domains import create_domain, delete_domain, list_domain_files, list_domains
from core.experience_pipeline import (
    merge_experience,
    prepare_save,
    save_new_experience,
    summarize_field_diff,
)
from core.experience_store import ExperienceDraft, ExperienceStore

# 对话历史上限条数
MAX_CHAT_HISTORY = 50
VIEW_QA = "💬 智能问答"
VIEW_BROWSE = "📚 文档浏览"
VIEW_UPLOAD = "📥 文档上传"
VIEW_EXPERIENCE = "🧠 经验库"

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="🧠 个人知识库系统",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# 自定义样式
# ============================================================
st.markdown("""
<style>
    .stApp {
        max-width: 1200px;
        margin: 0 auto;
    }
    .source-box {
        background-color: #f0f2f6;
        border-radius: 8px;
        padding: 12px;
        margin: 4px 0;
        font-size: 0.9em;
    }
    .domain-badge {
        background-color: #e8f4fd;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 0.85em;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# Session State 初始化
# ============================================================
def init_session_state():
    """初始化 Streamlit 会话状态"""
    if "current_domain" not in st.session_state:
        st.session_state.current_domain = "默认"
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "current_view" not in st.session_state:
        st.session_state.current_view = VIEW_QA
    if "experience_drafts" not in st.session_state:
        st.session_state.experience_drafts = {}
    if "experience_reviews" not in st.session_state:
        st.session_state.experience_reviews = {}


init_session_state()


def make_chat_entry(question: str, answer: str, sources: list[dict]) -> dict:
    """Build a deterministic conversation record for stable Streamlit keys."""
    source_payload = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    stable_id = hashlib.sha256(
        f"{question}\n{answer}\n{source_payload}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "id": stable_id,
        "question": question,
        "answer": answer,
        "sources": sources,
    }


def select_experience_sources(sources: list[dict], selections: list[bool]) -> list[dict]:
    """Keep only citations explicitly selected for cloud drafting."""
    return [source for source, selected in zip(sources, selections) if selected]


def get_experience_store() -> ExperienceStore:
    """Return the local experience store, initializing SQLite only when needed."""
    if "experience_store" not in st.session_state:
        st.session_state.experience_store = ExperienceStore(EXPERIENCE_DB_PATH)
    return st.session_state.experience_store


def get_experience_index():
    """Create the independent semantic index only when duplicate review needs it."""
    if "experience_index" not in st.session_state:
        from core.experience_index import ExperienceIndex

        st.session_state.experience_index = ExperienceIndex(get_experience_store())
    return st.session_state.experience_index


def get_document_store():
    """Initialize the document archive only when a document workflow is used."""
    if "document_store" not in st.session_state:
        from core.document_store import DocumentStore

        st.session_state.document_store = DocumentStore(DOCUMENT_DB_PATH)
    return st.session_state.document_store


def get_observability_store():
    """Initialize local query traces only when a question is asked."""
    if "observability_store" not in st.session_state:
        from core.observability import QueryTraceStore

        st.session_state.observability_store = QueryTraceStore(OBSERVABILITY_DB_PATH)
    return st.session_state.observability_store


def _source_payload(doc) -> dict:
    """Build a citation payload with archive and projection provenance."""
    from core.observability import document_status_label

    record = get_document_store().get(doc.document_id) if doc.document_id else None
    status = record.status if record else "unknown"
    index_pending = bool(record.index_pending) if record else False
    return {
        "source": doc.document_name,
        "document_id": doc.document_id,
        "version": doc.document_version,
        "section": doc.section_title,
        "excerpt": doc.content[:500],
        "page": doc.page,
        "score": doc.score,
        "status": status,
        "status_label": document_status_label(status, index_pending),
        "index_pending": index_pending,
    }


def render_source_provenance(sources: list[dict]) -> None:
    """Show citations and their local archive/index state."""
    if not sources:
        return
    st.caption("引用来源与索引状态")
    for index, source in enumerate(sources, 1):
        location = source.get("section") or "未标注"
        if source.get("page") is not None:
            location += f" / 第 {source['page']} 页"
        status_label = source.get("status_label", "未知状态")
        st.markdown(
            f"**[{index}]** `{source.get('source', '未知来源')}` · "
            f"v{source.get('version', '未知')} · {location} · {status_label}"
        )
        if source.get("index_pending"):
            st.warning("该文档已保存，但索引待重建；回答引用来自当前可用的本地归档。")


def _record_query_trace(
    *,
    domain: str,
    retrieval_count: int,
    selected_versions: list[tuple[str, str]],
    refusal_reason: str | None,
) -> None:
    """Keep trace initialization and writes outside the answer failure path."""
    try:
        from core.observability import record_query_trace_safely

        record_query_trace_safely(
            get_observability_store(),
            domain=domain,
            retrieval_count=retrieval_count,
            selected_versions=selected_versions,
            refusal_reason=refusal_reason,
        )
    except Exception as error:
        logger.warning("问答观测初始化失败，已忽略: %s", type(error).__name__)


def get_document_retriever(domain: str):
    """Build a hybrid retriever lazily for the selected domain."""
    from core.hybrid_retriever import HybridRetriever
    from core.retriever import get_vectorstore

    def semantic_search(query: str, selected_domain: str, top_k: int):
        try:
            return get_vectorstore(selected_domain).similarity_search_with_score(query, k=top_k)
        except Exception as error:
            logger.warning("语义索引不可用，继续使用关键词检索: %s", type(error).__name__)
            return []

    return HybridRetriever(get_document_store(), semantic_search, top_k=RETRIEVAL_TOP_K)


def render_active_view(view: str) -> None:
    """Render exactly one main view for the current rerun."""
    if view == VIEW_BROWSE:
        render_browse_section()
    elif view == VIEW_UPLOAD:
        render_upload_section()
    elif view == VIEW_EXPERIENCE:
        render_experience_section()
    else:
        render_qa_section()


# ============================================================
# 领域名校验
# ============================================================
def validate_domain_name(name: str) -> Optional[str]:
    """
    校验领域名称合法性，返回错误信息或 None（合法时）
    """
    if not name:
        return "请输入领域名称"
    # Windows 禁止字符
    if re.search(r'[<>:"|?*\\]', name):
        return "领域名称包含非法字符（禁止: < > : \" | ? * \\）"
    # 路径遍历
    if ".." in name:
        return "领域名称不能包含 '..'"
    if len(name) > 100:
        return "领域名称过长（最多100字符）"
    return None


# ============================================================
# 侧边栏 — 领域管理
# ============================================================
def render_sidebar():
    """渲染侧边栏：领域管理 + 系统信息"""
    with st.sidebar:
        st.title("🧠 知识库管理")
        st.divider()

        # --- 领域选择 ---
        st.subheader("📂 领域切换")
        domains = list_domains()

        # 如果没有领域，自动创建默认领域
        if not domains:
            create_domain("默认")
            domains = ["默认"]

        def _on_domain_change():
            st.session_state.current_domain = st.session_state.domain_selector

        current_domain = st.selectbox(
            "选择领域",
            options=domains,
            index=domains.index(st.session_state.current_domain)
            if st.session_state.current_domain in domains
            else 0,
            key="domain_selector",
            on_change=_on_domain_change,
        )
        # on_change 回调处理用户手动切换；此处覆盖上传后自动切换的场景
        st.session_state.current_domain = current_domain

        # 统计需要打开向量库，只有用户主动请求时才加载
        stats_key = f"domain_stats_{current_domain}"
        if st.button("加载片段统计", key=f"load_{stats_key}", use_container_width=True):
            from core.retriever import get_domain_stats

            st.session_state[stats_key] = get_domain_stats(current_domain)
        if stats_key in st.session_state:
            st.caption(f"📊 文档片段数: **{st.session_state[stats_key]['document_count']}**")

        st.divider()

        # --- 创建领域 ---
        st.subheader("➕ 创建新领域")
        new_domain_name = st.text_input("领域名称", placeholder="例如：医学、编程、历史...")
        if st.button("➕ 创建领域", use_container_width=True, type="primary"):
            stripped = new_domain_name.strip()
            error = validate_domain_name(stripped)
            if error:
                st.error(error)
            elif create_domain(stripped):
                st.success(f"领域 '{stripped}' 创建成功！")
                st.rerun()
            else:
                st.warning(f"领域 '{stripped}' 已存在")

        st.divider()

        # --- 删除领域 ---
        st.subheader("🗑️ 删除领域")
        if len(domains) > 1:
            domain_to_delete = st.selectbox(
                "选择要删除的领域",
                options=[d for d in domains if d != "默认"],
                key="delete_domain_selector",
            )
            if st.button("🗑️ 删除", use_container_width=True, type="secondary"):
                if domain_to_delete:
                    delete_domain(domain_to_delete)
                    if st.session_state.current_domain == domain_to_delete:
                        st.session_state.current_domain = "默认"
                    st.success(f"领域 '{domain_to_delete}' 已删除")
                    st.rerun()
        else:
            st.caption("至少保留一个领域")

        st.divider()

        # --- 系统信息 ---
        st.subheader("ℹ️ 系统信息")
        st.caption(f"当前领域: **{current_domain}**")
        st.caption(f"检索数量: **Top-{RETRIEVAL_TOP_K}**")
        st.caption(f"数据目录: `data/domains/`")


# ============================================================
# 主界面 — 文档浏览
# ============================================================
def render_browse_section():
    """渲染文档浏览区域，展示当前领域中已有的文档"""
    st.header("📚 文档浏览")

    current_domain = st.session_state.current_domain
    st.info(f"📂 当前领域: **{current_domain}**")

    st.subheader("🔧 知识索引维护")
    maintain_col1, maintain_col2 = st.columns(2)
    with maintain_col1:
        migrate_clicked = st.button("迁移现有资料", key="migrate_documents")
    with maintain_col2:
        rebuild_clicked = st.button("重建知识索引", key="rebuild_document_indexes")
    if migrate_clicked or rebuild_clicked:
        from core.migration import migrate_domain, rebuild_indexes

        vectorstore = None
        try:
            from core.retriever import get_vectorstore

            vectorstore = get_vectorstore(current_domain)
        except Exception as error:
            logger.warning("语义索引初始化失败，将仅维护 SQLite/FTS5: %s", type(error).__name__)
            st.warning("语义索引暂不可用，已继续维护关键词索引；稍后可重建。")
        if migrate_clicked:
            report = migrate_domain(
                current_domain,
                raw_dir=os.path.join(RAW_DIR, current_domain),
                store=get_document_store(),
                vectorstore=vectorstore,
            )
            if report.success_count:
                st.success(f"迁移成功：{report.success_count} 个文件")
            if report.failure_count:
                st.error(f"迁移失败：{report.failure_count} 个文件")
            if report.pending_count:
                st.warning(f"需要重试：{report.pending_count} 个文件待重建索引")
            for failure in report.failures:
                st.error(f"{failure['file']}: {failure['reason']}")
        if rebuild_clicked:
            report = rebuild_indexes(
                store=get_document_store(), vectorstore=vectorstore, domains=[current_domain]
            )
            if report.success_count:
                st.success(f"重建成功：{report.success_count} 个文档")
            if report.failure_count:
                st.error(f"重建失败：{report.failure_count} 个文档")
            if report.pending_count:
                st.warning(f"需要重试：{report.pending_count} 个文档待重建索引")
            if report.recovered:
                st.caption(f"已恢复：{', '.join(report.recovered)}")
            if report.missing:
                st.warning(f"缺失切片：{', '.join(report.missing)}")
            if report.retry_needed:
                st.warning(f"重试清单：{', '.join(report.retry_needed)}")
            for failure in report.failures:
                st.error(f"{failure['file']}: {failure['reason']}")

    st.subheader("💾 本地备份与恢复")
    st.caption("备份包含文档档案、经验卡片、可用的观测记录和原始资料；恢复前会先校验清单与 SHA-256。")
    backup_col1, backup_col2 = st.columns(2)
    with backup_col1:
        backup_path = st.text_input(
            "备份目录",
            value=os.path.join(BACKUP_DIR, "latest"),
            key="backup_path_input",
        )
        if st.button("创建并校验备份", key="create_local_backup", use_container_width=True):
            from core.backup import create_backup

            try:
                report = create_backup(
                    backup_path,
                    document_db_path=DOCUMENT_DB_PATH,
                    experience_db_path=EXPERIENCE_DB_PATH,
                    observability_db_path=OBSERVABILITY_DB_PATH,
                    raw_dir=RAW_DIR,
                )
                st.success(f"备份完成并通过校验：{len(report.files)} 个文件")
                st.caption(f"清单：{report.manifest_path}")
            except Exception as error:
                st.error(f"备份失败：{error}")
    with backup_col2:
        restore_source = st.text_input(
            "备份来源目录",
            value=os.path.join(BACKUP_DIR, "latest"),
            key="restore_source_input",
        )
        restore_destination = st.text_input(
            "恢复目标目录",
            value=os.path.join(BACKUP_DIR, "restored"),
            key="restore_destination_input",
        )
        confirm_overwrite = st.checkbox(
            "确认覆盖非空恢复目标",
            value=False,
            key="restore_confirm_overwrite",
        )
        if st.button("校验并恢复", key="restore_local_backup", use_container_width=True):
            from core.backup import restore_backup

            try:
                report = restore_backup(
                    restore_source,
                    restore_destination,
                    confirm_overwrite=confirm_overwrite,
                )
                st.success(f"恢复完成：{len(report.recovered)} 个文件")
                if report.missing:
                    st.warning(f"缺失：{', '.join(report.missing)}")
                if report.retry_needed:
                    st.warning(f"需要重试：{', '.join(report.retry_needed)}")
            except Exception as error:
                st.error(f"恢复未执行：{error}")
                report = getattr(error, "report", None)
                if report is not None:
                    if report.missing:
                        st.warning(f"缺失：{', '.join(report.missing)}")
                    if report.integrity_failures:
                        st.warning(f"完整性失败：{', '.join(report.integrity_failures)}")
                    if report.retry_needed:
                        st.warning(f"需要重试：{', '.join(report.retry_needed)}")

    from core.observability import document_status_label, get_index_health

    archive_store = get_document_store()
    health = get_index_health(archive_store, current_domain)
    st.caption(
        "索引健康："
        f"{health['status']} · 当前生效 {health['active']} · 历史版本 {health['superseded']} · "
        f"失败 {health['failed']} · 待重建 {health['index_pending']}"
    )

    # 获取领域内的文件列表
    files = list_domain_files(current_domain)

    if not files:
        st.warning(f"领域「{current_domain}」中暂无文档，请先上传文档")
        return

    # 统计信息
    col_stat1, col_stat2, col_stat3 = st.columns(3)
    with col_stat1:
        st.metric("📄 文档数量", len(files))
    with col_stat2:
        from core.retriever import get_domain_stats

        stats = get_domain_stats(current_domain)
        st.metric("🧩 文本片段", stats["document_count"])
    with col_stat3:
        exts = set(f["ext"] for f in files)
        st.metric("📁 文件类型", len(exts))

    st.divider()

    # 搜索过滤
    search_keyword = st.text_input("🔍 搜索文件名", placeholder="输入关键词过滤...", key="browse_search")

    # 过滤文件
    filtered_files = files
    if search_keyword.strip():
        keyword = search_keyword.strip().lower()
        filtered_files = [f for f in files if keyword in f["name"].lower()]

        if not filtered_files:
            st.warning(f"未找到匹配「{search_keyword}」的文件")
            return

    # 文件列表展示
    st.subheader(f"📋 文件列表 ({len(filtered_files)} 个)")
    records = archive_store.list_documents(domain=current_domain, include_superseded=True)
    records_by_name = {}
    for record in records:
        records_by_name.setdefault(record.name, record)

    # 分页设置
    PAGE_SIZE = 10
    total_pages = max(1, (len(filtered_files) + PAGE_SIZE - 1) // PAGE_SIZE)

    if "browse_page" not in st.session_state:
        st.session_state.browse_page = 1

    col_page1, col_page2, col_page3 = st.columns([1, 3, 1])
    with col_page1:
        if st.button("⬅️ 上一页", disabled=st.session_state.browse_page <= 1):
            st.session_state.browse_page -= 1
            st.rerun()
    with col_page2:
        st.caption(f"第 {st.session_state.browse_page} / {total_pages} 页")
    with col_page3:
        if st.button("下一页 ➡️", disabled=st.session_state.browse_page >= total_pages):
            st.session_state.browse_page += 1
            st.rerun()

    # 计算当前页的文件范围
    start_idx = (st.session_state.browse_page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(filtered_files))
    page_files = filtered_files[start_idx:end_idx]

    # 展示文件卡片
    for i, file_info in enumerate(page_files):
        with st.container():
            col1, col2, col3 = st.columns([4, 2, 1])
            with col1:
                st.markdown(f"{file_info['icon']} **{file_info['name']}**")
            with col2:
                record = records_by_name.get(file_info["name"])
                status = (
                    document_status_label(record.status, record.index_pending)
                    if record
                    else "未入库"
                )
                version = f" · v{record.version}" if record else ""
                st.caption(f"大小: {file_info['size']} · {status}{version}")
            with col3:
                # 删除按钮
                if st.button("🗑️", key=f"del_{start_idx + i}", help=f"删除 {file_info['name']}"):
                    try:
                        os.remove(file_info["path"])
                        st.success(f"已删除: {file_info['name']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"删除失败: {e}")
            st.divider()

    # 文件类型统计饼图
    st.subheader("📊 文件类型分布")
    ext_count = {}
    for f in files:
        ext = f["ext"]
        ext_count[ext] = ext_count.get(ext, 0) + 1

    # 使用简单的柱状图展示
    chart_data = {ext: count for ext, count in sorted(ext_count.items(), key=lambda x: -x[1])}
    st.bar_chart(chart_data)


# ============================================================
# 主界面 — 文档上传
# ============================================================
def render_upload_section():
    """Review a local batch before committing accepted documents."""
    st.header("📥 文档上传")

    domains = list_domains()
    if not domains:
        domains = ["默认"]

    custom_domain = st.selectbox(
        "选择领域",
        options=domains,
        index=domains.index(st.session_state.current_domain)
        if st.session_state.current_domain in domains
        else 0,
        key="upload_domain_selector",
    )

    upload_col, preview_col = st.columns([2, 1])
    with upload_col:
        uploaded_files = st.file_uploader(
            "选择文件（可多选）",
            help="可同时选择多个文件；不支持的格式会在预览中标出，不会入库",
            accept_multiple_files=True,
        )
    with preview_col:
        st.write("")
        st.write("")
        preview_clicked = st.button(
            "🔎 生成批量预览",
            use_container_width=True,
            disabled=not uploaded_files,
        )

    metadata_col1, metadata_col2, metadata_col3 = st.columns(3)
    with metadata_col1:
        category = st.text_input("分类", value="其他", key="batch_category")
    with metadata_col2:
        owner = st.text_input("责任人", value="", key="batch_owner")
    with metadata_col3:
        source = st.text_input("来源", value="upload", key="batch_source")

    if uploaded_files and preview_clicked:
        from core.metadata import preview_batch

        target_domain = custom_domain.strip() or "默认"
        error = validate_domain_name(target_domain)
        if error:
            st.error(error)
            return
        previous_staging = st.session_state.pop("batch_staging_dir", None)
        if previous_staging:
            shutil.rmtree(previous_staging, ignore_errors=True)
        staging_dir = Path(tempfile.mkdtemp(prefix="kb-batch-"))
        try:
            paths = []
            used_names: set[str] = set()
            for index, uploaded_file in enumerate(uploaded_files):
                safe_name = Path(uploaded_file.name).name or f"upload-{index}"
                if safe_name in used_names:
                    raise ValueError(f"批次内文件名重复: {safe_name}")
                used_names.add(safe_name)
                path = staging_dir / safe_name
                path.write_bytes(uploaded_file.getbuffer())
                paths.append(path)
            st.session_state.batch_preview = preview_batch(
                paths,
                domain=target_domain,
                store=get_document_store(),
                category=category,
                owner=owner,
                source=source,
            )
            st.session_state.batch_staging_dir = str(staging_dir)
            st.rerun()
        except Exception as error:
            shutil.rmtree(staging_dir, ignore_errors=True)
            st.error(f"预览失败：{error}")

    preview = st.session_state.get("batch_preview")
    if preview is None:
        return

    st.subheader("批量入库预览")
    st.caption(
        f"领域：{preview.domain} · 接受入库：{preview.accepted_count} / {len(preview.items)}；"
        "预览不会写入数据库。"
    )
    for warning in preview.metadata.warnings:
        st.warning(warning)
    st.caption(
        f"分类：{preview.metadata.category} · 责任人：{preview.metadata.owner or '未填写'} · "
        f"来源：{preview.metadata.source} · 更新时间：{preview.metadata.updated_at}"
    )
    action_labels = {
        "new": "新增",
        "replace": "替换版本",
        "duplicate": "重复跳过",
        "unsupported": "不支持",
        "invalid": "解析失败",
        "missing": "文件缺失",
    }
    for item in preview.items:
        version = f" · 拟用 v{item.proposed_version}" if item.proposed_version else ""
        st.write(
            f"**{item.name}** · {action_labels.get(item.action, item.action)}{version} · {item.reason}"
        )

    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        confirm_clicked = st.button(
            "✅ 确认并入库",
            type="primary",
            use_container_width=True,
            disabled=preview.accepted_count == 0,
        )
    with cancel_col:
        cancel_clicked = st.button("取消本批次", use_container_width=True)

    if cancel_clicked:
        staging_dir = st.session_state.pop("batch_staging_dir", None)
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        st.session_state.pop("batch_preview", None)
        st.rerun()

    if confirm_clicked:
        from core.metadata import execute_batch

        if preview.domain not in list_domains() and not create_domain(preview.domain):
            st.error(f"创建领域「{preview.domain}」失败")
            return
        vectorstore = None
        try:
            from core.retriever import get_vectorstore

            vectorstore = get_vectorstore(preview.domain)
        except Exception as error:
            logger.warning("语义索引初始化失败，批量入库将保留关键词索引: %s", type(error).__name__)
            st.warning("语义索引暂不可用，文档仍会保存并标记待重建。")
        report = execute_batch(preview, store=get_document_store(), vectorstore=vectorstore)
        st.session_state.current_domain = preview.domain
        if report.successes:
            st.success(f"成功入库：{len(report.successes)} 个文件")
        if report.duplicates:
            st.info(f"重复跳过：{', '.join(report.duplicates)}")
        if report.pending:
            st.warning(f"索引待重建：{', '.join(report.pending)}")
        if report.retry_needed:
            st.warning(f"需要重试：{', '.join(report.retry_needed)}")
        for failure in report.failures:
            st.error(f"{failure['file']}: {failure['reason']}")
        staging_dir = st.session_state.pop("batch_staging_dir", None)
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        st.session_state.pop("batch_preview", None)


# ============================================================
# 主界面 — 智能问答
# ============================================================
def render_qa_section():
    """渲染智能问答区域"""
    from core.generator import generate_answer_stream
    from core.generator import REFUSAL_MESSAGE

    st.header("💬 智能问答")

    # 问题输入
    question = st.text_area(
        "输入你的问题",
        placeholder="例如：这份文档的主要内容是什么？",
        height=80,
    )

    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        ask_btn = st.button("🔍 提问", use_container_width=True, type="primary", disabled=not question.strip())
    with col2:
        clear_btn = st.button("🗑️ 清空对话", use_container_width=True)
        if clear_btn:
            st.session_state.chat_history = []
            st.rerun()

    if ask_btn and question.strip():
        with st.spinner("正在检索相关文档..."):
            retrieved_docs = get_document_retriever(st.session_state.current_domain).search(
                question, domain=st.session_state.current_domain, top_k=RETRIEVAL_TOP_K
            )

        if not retrieved_docs:
            _record_query_trace(
                domain=st.session_state.current_domain,
                retrieval_count=0,
                selected_versions=[],
                refusal_reason="no_relevant_sources",
            )
            st.warning(REFUSAL_MESSAGE)
            return

        selected_versions = [
            (doc.document_name, doc.document_version) for doc in retrieved_docs
        ]
        sources = [_source_payload(doc) for doc in retrieved_docs]

        # 显示检索结果
        with st.expander("📋 检索到的相关文档", expanded=False):
            for i, doc in enumerate(retrieved_docs, 1):
                source = sources[i - 1]
                location = f"{doc.section_title or '未标注'}"
                if doc.page is not None:
                    location += f" / 第 {doc.page} 页"
                st.markdown(
                    f"**[{i}] 来源:** `{doc.document_name}` · v{doc.document_version} · {location} "
                    f"· {source['status_label']} (综合相关度: {doc.score:.3f})"
                )
                st.caption(f"关键词 {doc.keyword_score:.3f} · 语义 {doc.semantic_score:.3f}")
                st.markdown(f"> {doc.content[:300]}...")
                st.divider()

        # 生成回答（流式）
        st.subheader("📝 回答")
        # 流式输出
        answer_container = st.empty()
        full_answer = ""
        for chunk in generate_answer_stream(question, retrieved_docs):
            full_answer += chunk
            answer_container.markdown(full_answer)

        _record_query_trace(
            domain=st.session_state.current_domain,
            retrieval_count=len(retrieved_docs),
            selected_versions=selected_versions,
            refusal_reason=None,
        )
        # 保存到对话历史（保留最近 MAX_CHAT_HISTORY 条）
        st.session_state.chat_history.append(make_chat_entry(question, full_answer, sources))
        if len(st.session_state.chat_history) > MAX_CHAT_HISTORY:
            st.session_state.chat_history = st.session_state.chat_history[-MAX_CHAT_HISTORY:]

    # 显示对话历史
    if st.session_state.chat_history:
        st.divider()
        st.subheader("📜 对话历史")
        for i, chat in enumerate(reversed(st.session_state.chat_history), 1):
            with st.chat_message("user"):
                st.write(chat["question"])
            with st.chat_message("assistant"):
                st.write(chat["answer"])
                render_source_provenance(chat.get("sources", []))
                render_experience_capture(chat)


def _draft_to_dict(draft: ExperienceDraft) -> dict:
    return {
        "title": draft.title,
        "scenario": draft.scenario,
        "conclusion": draft.conclusion,
        "steps": list(draft.steps),
        "tags": list(draft.tags),
        "sources": list(draft.sources),
        "question": draft.question,
        "answer_excerpt": draft.answer_excerpt,
    }


def _draft_from_dict(values: dict) -> ExperienceDraft:
    return ExperienceDraft(
        title=values["title"],
        scenario=values["scenario"],
        conclusion=values["conclusion"],
        steps=list(values["steps"]),
        tags=list(values["tags"]),
        sources=list(values["sources"]),
        question=values.get("question", ""),
        answer_excerpt=values.get("answer_excerpt", ""),
    )


def render_experience_capture(chat: dict) -> None:
    """Render capture, edit, duplicate review, and save actions for one answer."""
    chat_id = chat["id"]
    drafts = st.session_state.experience_drafts
    reviews = st.session_state.experience_reviews

    if chat_id not in drafts and chat_id not in reviews:
        if st.button("🧠 沉淀为经验", key=f"capture_{chat_id}"):
            st.session_state[f"capture_open_{chat_id}"] = True

    if st.session_state.get(f"capture_open_{chat_id}") and chat_id not in drafts:
        st.info("将仅发送当前问题、回答和你选定的引用片段到云端进行整理。")
        source_selections = []
        sources = chat.get("sources", [])
        if sources:
            st.caption("选择需要发送给云端的引用片段（可不选）")
            for index, source in enumerate(sources):
                source_name = source.get("source", "未知来源")
                excerpt = source.get("excerpt", "").replace("\n", " ")[:120]
                source_selections.append(
                    st.checkbox(
                        f"引用 {index + 1} · {source_name}: {excerpt}",
                        value=False,
                        key=f"share_source_{chat_id}_{index}",
                    )
                )
        confirmed = st.checkbox(
            "我确认发送当前问答和引用片段",
            key=f"share_confirm_{chat_id}",
        )
        if st.button(
            "生成经验草稿",
            key=f"draft_{chat_id}",
            disabled=not confirmed,
            type="primary",
        ):
            from core.agent import AgentError, draft_experience

            try:
                draft = draft_experience(
                    chat["question"],
                    chat["answer"],
                    select_experience_sources(sources, source_selections),
                )
                drafts[chat_id] = _draft_to_dict(draft)
                st.session_state[f"capture_open_{chat_id}"] = False
                st.rerun()
            except AgentError as error:
                st.error(str(error))

    if chat_id in drafts:
        _render_experience_draft_editor(chat, drafts[chat_id])
    elif chat_id in reviews:
        _render_experience_review(chat, reviews[chat_id])


def _render_experience_draft_editor(chat: dict, values: dict) -> None:
    chat_id = chat["id"]
    st.markdown("**经验草稿（确认后才会保存）**")
    title = st.text_input("标题", value=values["title"], key=f"title_{chat_id}")
    scenario = st.text_area("问题 / 场景", value=values["scenario"], key=f"scenario_{chat_id}")
    conclusion = st.text_area("核心结论", value=values["conclusion"], key=f"conclusion_{chat_id}")
    steps_text = st.text_area(
        "操作步骤（每行一步）",
        value="\n".join(values["steps"]),
        key=f"steps_{chat_id}",
    )
    tags_text = st.text_input(
        "标签（用逗号分隔）",
        value="、".join(values["tags"]),
        key=f"tags_{chat_id}",
    )
    st.caption("引用来源")
    for source in values["sources"]:
        st.caption(f"- {source.get('source', '未知来源')}: {source.get('excerpt', '')[:200]}")

    if st.button("检查重复并进入审核", key=f"review_{chat_id}", type="primary"):
        draft = ExperienceDraft(
            title=title.strip(),
            scenario=scenario.strip(),
            conclusion=conclusion.strip(),
            steps=[item.strip() for item in steps_text.splitlines() if item.strip()],
            tags=[item.strip() for item in re.split(r"[,，、]", tags_text) if item.strip()],
            sources=values["sources"],
            question=values.get("question", chat["question"]),
            answer_excerpt=values.get("answer_excerpt", chat["answer"][:1000]),
        )
        store = get_experience_store()
        index_available = True
        try:
            semantic_matches = get_experience_index().search(
                "\n".join([draft.title, draft.scenario, draft.conclusion])
            )
        except Exception as error:
            index_available = False
            semantic_matches = []
            logger.warning("经验相似检索不可用: %s", type(error).__name__)
            st.warning("相似经验检查暂不可用；保存后会标记为待重建索引。")

        result = prepare_save(store, draft, semantic_matches=semantic_matches)
        st.session_state.experience_reviews[chat_id] = {
            "draft": _draft_to_dict(draft),
            "kind": result.kind,
            "match_ids": [match.id for match in result.matches],
            "index_available": index_available,
        }
        del st.session_state.experience_drafts[chat_id]
        st.rerun()


def _render_experience_review(chat: dict, review: dict) -> None:
    chat_id = chat["id"]
    draft = _draft_from_dict(review["draft"])
    store = get_experience_store()
    matches = [store.get_required(match_id) for match_id in review["match_ids"]]
    if review["kind"] == "exact_duplicate":
        st.warning("发现内容完全相同的经验。你可以放弃、仍保存为新经验，或返回修改。")
    elif matches:
        st.warning("发现相近经验，请对照后选择处理方式。")

    for index, match in enumerate(matches):
        st.markdown(f"**相似经验 {index + 1}：{match.title}**")
        if review["kind"] != "exact_duplicate":
            diff = summarize_field_diff(match, draft)
            for field, change in diff.items():
                if change["changed"]:
                    st.caption(f"{field}: {change['before']} → {change['after']}")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("保存为新经验", key=f"save_new_{chat_id}", type="primary"):
            index = _review_index_or_none(review)
            saved = save_new_experience(store, draft, index=index)
            if index is None:
                store.set_index_pending(saved.id, True)
            _clear_experience_state(chat_id)
            st.success("经验已保存")
            st.rerun()
    with col2:
        if matches and st.button("合并更新", key=f"merge_{chat_id}"):
            index = _review_index_or_none(review)
            saved = merge_experience(store, matches[0].id, draft, index=index)
            if index is None:
                store.set_index_pending(saved.id, True)
            _clear_experience_state(chat_id)
            st.success("经验已合并更新")
            st.rerun()
    with col3:
        if st.button("放弃", key=f"abandon_{chat_id}"):
            _clear_experience_state(chat_id)
            st.rerun()


def _clear_experience_state(chat_id: str) -> None:
    st.session_state.experience_drafts.pop(chat_id, None)
    st.session_state.experience_reviews.pop(chat_id, None)
    st.session_state.pop(f"capture_open_{chat_id}", None)


def _review_index_or_none(review: dict):
    if not review.get("index_available", False):
        return None
    try:
        return get_experience_index()
    except Exception as error:
        logger.warning("经验索引初始化失败: %s", type(error).__name__)
        return None


def render_experience_section() -> None:
    """Browse, search, inspect, archive, and restore saved experiences."""
    st.header("🧠 经验库")
    store = get_experience_store()
    if st.button("重建经验索引", key="rebuild_experience_index"):
        try:
            get_experience_index().rebuild()
            st.success("经验索引已重建")
        except Exception as error:
            logger.warning("经验索引重建失败: %s", type(error).__name__)
            st.error("经验索引重建失败，请稍后重试")
    query = st.text_input("搜索标题、场景、结论或标签", key="experience_search")
    include_archived = st.checkbox("显示已归档", key="experience_archived")
    cards = store.search(query, include_archived=include_archived)
    if not cards:
        st.info("暂无经验。请在问答结果下点击“沉淀为经验”。")
        return

    for card in cards:
        with st.expander(f"{card.title} · {', '.join(card.tags)}"):
            st.write(card.conclusion)
            st.caption(f"场景：{card.scenario}")
            if card.steps:
                st.markdown("**步骤**")
                for step in card.steps:
                    st.write(f"- {step}")
            pending = "；索引待重建" if card.index_pending else ""
            st.caption(f"状态：{card.status}；更新时间：{card.updated_at}{pending}")
            st.caption("来源")
            for source in store.get_sources(card.id):
                st.caption(f"- {source['source']}: {source['excerpt'][:200]}")
            st.caption("版本历史")
            for version in store.get_versions(card.id):
                st.caption(f"v{version['version']} · {version['change_type']} · {version['created_at']}")
            action_key = f"archive_{card.id}" if card.status == "active" else f"restore_{card.id}"
            action_label = "归档" if card.status == "active" else "恢复"
            if st.button(action_label, key=action_key):
                (store.archive if card.status == "active" else store.restore)(card.id)
                st.rerun()


# ============================================================
# 主界面布局
# ============================================================
def main():
    """主函数"""
    # 侧边栏
    render_sidebar()

    st.session_state.current_view = st.radio(
        "功能视图",
        [VIEW_QA, VIEW_BROWSE, VIEW_UPLOAD, VIEW_EXPERIENCE],
        key="main_view_selector",
        horizontal=True,
    )
    render_active_view(st.session_state.current_view)


if __name__ == "__main__":
    main()
