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
from typing import Optional

import streamlit as st

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import RAW_DIR, RETRIEVAL_TOP_K
from core.loader import load_document
from core.splitter import split_documents
from core.retriever import (
    add_documents,
    search_with_score,
    list_domains,
    create_domain,
    delete_domain,
    get_domain_stats,
    list_domain_files,
)
from core.generator import generate_answer_stream

# 对话历史上限条数
MAX_CHAT_HISTORY = 50

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


init_session_state()


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

        # 显示领域统计
        stats = get_domain_stats(current_domain)
        st.caption(f"📊 文档片段数: **{stats['document_count']}**")

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
                st.caption(f"大小: {file_info['size']}")
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
    """渲染文档上传区域"""
    st.header("📥 文档上传")

    # 领域选择（与侧边栏同步）
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

    col1, col2 = st.columns([2, 1])

    with col1:
        uploaded_files = st.file_uploader(
            "选择文件（可多选）",
            type=["pdf", "docx", "doc", "pptx", "ppt", "md", "txt", "jpg", "jpeg", "png", "bmp"],
            help="支持 PDF、Word、PPT、Markdown、TXT、图片格式，可同时选择多个文件",
            accept_multiple_files=True,
        )

    with col2:
        st.write("")
        st.write("")
        process_btn = st.button("📥 处理并入库", use_container_width=True, type="primary", disabled=not uploaded_files)

    if uploaded_files and process_btn:
        # 使用自定义领域名称
        target_domain = custom_domain.strip() or "默认"

        # 校验领域名称
        error = validate_domain_name(target_domain)
        if error:
            st.error(error)
            return

        # 自动创建领域（如果不存在）
        domains = list_domains()
        if target_domain not in domains:
            if not create_domain(target_domain):
                st.error(f"创建领域「{target_domain}」失败")
                return

        total_count = 0
        success_names = []
        fail_details = []  # 存储 (文件名, 失败原因) 元组

        progress = st.progress(0, text="开始处理文档...")

        for i, uploaded_file in enumerate(uploaded_files):
            progress.progress(
                (i) / len(uploaded_files),
                text=f"正在处理第 {i + 1}/{len(uploaded_files)} 个文件: {uploaded_file.name}",
            )
            tmp_path = None
            try:
                # 保存上传文件到临时目录
                ext = os.path.splitext(uploaded_file.name)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(uploaded_file.getbuffer())
                    tmp_path = tmp.name

                # 同时保存到 raw 目录
                raw_path = os.path.join(RAW_DIR, target_domain, uploaded_file.name)
                os.makedirs(os.path.dirname(raw_path), exist_ok=True)
                with open(raw_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                # 加载文档
                docs = load_document(tmp_path)
                if not docs:
                    reason = "文档内容为空或格式不支持，无法提取文本"
                    logger.warning(f"未能从文档中提取内容: {uploaded_file.name}")
                    fail_details.append((uploaded_file.name, reason))
                    continue

                # 切分文本
                chunks = split_documents(docs)
                if not chunks:
                    reason = "文档切分后无有效文本片段"
                    logger.warning(f"文本切分后无内容: {uploaded_file.name}")
                    fail_details.append((uploaded_file.name, reason))
                    continue

                # 添加到向量库
                count = add_documents(chunks, domain=target_domain)
                total_count += count
                success_names.append(uploaded_file.name)

            except Exception as e:
                reason = f"处理异常: {str(e)}"
                logger.exception(f"文档处理异常: {uploaded_file.name}")
                fail_details.append((uploaded_file.name, reason))
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        progress.progress(1.0, text="处理完成")

        # 更新当前领域（下一帧侧边栏 selectbox 会自动同步）
        st.session_state.current_domain = target_domain

        # 汇总结果
        if success_names:
            st.success(f"✅ 成功处理 **{len(success_names)}** 个文件，共添加 **{total_count}** 个文本片段到领域「{target_domain}」")
        if fail_details:
            st.warning(f"⚠️ 以下 {len(fail_details)} 个文件处理失败:")
            for name, reason in fail_details:
                st.error(f"❌ **{name}**: {reason}")


# ============================================================
# 主界面 — 智能问答
# ============================================================
def render_qa_section():
    """渲染智能问答区域"""
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
            # 检索相关文档
            retrieved_docs = search_with_score(
                question,
                domain=st.session_state.current_domain,
                top_k=RETRIEVAL_TOP_K,
            )

        if not retrieved_docs:
            st.warning("未找到相关文档，请先上传文档到当前领域")
            return

        # 显示检索结果
        with st.expander("📋 检索到的相关文档", expanded=False):
            for i, (doc, score) in enumerate(retrieved_docs, 1):
                source = doc.metadata.get("source", "未知来源")
                st.markdown(f"**[{i}] 来源:** `{os.path.basename(source)}` (相似度: {1 - score:.4f})")
                st.markdown(f"> {doc.page_content[:300]}...")
                st.divider()

        # 生成回答（流式）
        st.subheader("📝 回答")
        docs_only = [doc for doc, _ in retrieved_docs]

        # 流式输出
        answer_container = st.empty()
        full_answer = ""
        for chunk in generate_answer_stream(question, docs_only):
            full_answer += chunk
            answer_container.markdown(full_answer)

        # 保存到对话历史（保留最近 MAX_CHAT_HISTORY 条）
        st.session_state.chat_history.append({
            "question": question,
            "answer": full_answer,
            "sources": [(doc.metadata.get("source", ""), doc.page_content[:200]) for doc in docs_only],
        })
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


# ============================================================
# 主界面布局
# ============================================================
def main():
    """主函数"""
    # 侧边栏
    render_sidebar()

    # 主页面标签页
    tab1, tab2, tab3 = st.tabs(["💬 智能问答", "📚 文档浏览", "📥 文档上传"])

    with tab1:
        render_qa_section()

    with tab2:
        render_browse_section()

    with tab3:
        render_upload_section()


if __name__ == "__main__":
    main()
