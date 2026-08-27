# 🧠 个人知识库系统

一个基于 RAG（检索增强生成）的个人知识库系统，支持多领域管理、多格式文档上传和智能问答。

## ✨ 功能特性

- 📂 **多领域管理** — 创建和管理多个知识领域
- 📥 **多格式上传** — 支持 PDF、Word、PPT、Markdown、TXT、图片
- 🧾 **批量预览与元数据治理** — 入库前识别新增、版本替换、重复、解析失败和不支持格式，统一审核分类、责任人、来源、版本和更新时间
- 💬 **智能问答** — 基于 RAG 的语义检索和智能回答
- 🧾 **文档档案与版本** — SQLite 保存内容哈希、分类、责任人、来源和版本，旧版本保留但默认不召回
- 🧩 **结构化切片** — DOCX/Markdown 保留标题路径和表格，FAQ 保持一问一答，PDF 保留页码
- 🔀 **混合检索** — SQLite FTS5 关键词召回与 Chroma 语义召回融合，编号、日期和数字优先关键词
- 🛑 **低相关拒答** — 没有可靠资料时直接提示补充文档，不调用模型猜测
- 🔧 **显式迁移/重建** — 文档浏览页提供“迁移现有资料”和“重建知识索引”，启动时不扫描全量文件
- 💾 **可恢复备份** — 文档浏览页可备份 SQLite 档案、原始资料和可用观测记录，并在恢复前校验 manifest 与 SHA-256
- 📚 **文档浏览** — 查看领域内已有的文档列表
- 🔍 **语义搜索** — 基于向量数据库的相似度检索
- 🧠 **经验沉淀** — 从问答结果整理可编辑经验卡片，确认后保存
- ♻️ **重复处理** — 自动识别完全重复和相近经验，由用户决定合并、另存或放弃
- 🗂️ **经验管理** — 搜索、查看来源与版本、归档、恢复和重建经验索引

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置 LLM API：

```env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
LLM_MODEL=mimo-v2.5-pro
```

### 3. 启动系统

```bash
streamlit run app.py
```

访问 http://localhost:8501 即可使用。

## 📚 P0 本地知识库工作流

上传或迁移资料后，系统会在 `data/documents.db` 建立文档档案和 FTS5 关键词投影，并在用户触发时更新领域 Chroma 语义索引。相同内容会幂等复用；同名内容变化会生成新版本，默认问答只使用 active 版本。

问答结果包含文档名称、版本、章节/页码、片段摘要和关键词/语义贡献。综合相关度低于门槛时返回“知识库暂无相关内容，请补充或更新相关文档”。

历史资料不会在应用启动时自动扫描。请在“文档浏览”页点击“迁移现有资料”建立档案；索引异常时点击“重建知识索引”，原始文件不会被删除。经验沉淀仍是独立的用户确认流程，只发送用户明确选中的引用片段。

批量上传采用“生成预览 -> 人工确认 -> 逐项入库”流程。预览阶段不写数据库；同批重复、已入库重复、同名新版本、不支持格式和解析失败会分别标出。确认后每个文件独立处理，失败项不会阻塞其他文件，并会列入重试清单。

备份与恢复也在“文档浏览”页完成。创建备份时请选择一个空目录；恢复时填写明确的来源目录和目标目录，目标非空还要勾选覆盖确认。系统会先完整校验备份清单，校验失败不会触碰恢复目标。重建索引后页面会列出已恢复、缺失切片和需要重试的文档。

## 🧪 离线 RAG 评测

评测只检查本地检索结果，不调用云端 LLM，也不改变问答检索逻辑。公开脱敏 fixture 可重复运行：

```powershell
python -m core.evaluation --mode fixture --cases tests/fixtures/evaluation_cases.json --corpus tests/fixtures/evaluation_corpus.json
```

真实问题和基线放在本地 `data/evaluation/`，该目录已加入忽略规则，不会提交或上传。live 模式需要显式指定数据库、领域和基线；只有 `--accept-baseline` 会写入新基线，普通回归运行不会覆盖旧基线：

```powershell
python -m core.evaluation --mode live --cases data/evaluation/cases.json --database data/documents.db --domain 默认 --baseline data/evaluation/baseline.json
```

退出码 `0` 表示通过，`1` 表示质量指标低于阈值，`2` 表示案例、索引或基线配置错误，`3` 表示未捕获异常。报告只包含案例 ID、指标和安全失败原因，不输出问题原文、召回片段或密钥。

## 🧠 经验沉淀流程

1. 在问答结果下点击 **“沉淀为经验”**，确认发送当前问题、回答和选定引用片段。
2. 云端服务只负责整理当前问答，返回一张可编辑的经验草稿；修改标题、场景、结论、步骤和标签。
3. 点击 **“检查重复并进入审核”**。完全重复会优先提示；相近经验会展示对照信息。
4. 由你选择 **保存为新经验**、**合并更新** 或 **放弃**。未确认的草稿不会写入经验库。

经验卡片保存在本地，来源和每次变更都会保留。经验库页面支持按内容搜索、查看来源与版本、归档和恢复。经验相似检查或点击 **“重建经验索引”** 时才会加载本地语义模型；索引暂时不可用时，经验仍会保存，并标记为待重建。

## 📁 项目结构

```
个人知识库系统/
├── app.py              # Streamlit 主入口
├── config.py           # 全局配置
├── requirements.txt    # 依赖列表
├── .env.example        # 环境变量模板
├── core/               # 核心模块
│   ├── loader.py       # 文档加载器
│   ├── splitter.py     # 结构化文本切分
│   ├── document_store.py # SQLite 文档档案与 FTS5
│   ├── ingestion.py    # 版本化入库与索引同步
│   ├── metadata.py     # 元数据规范、批量预览与逐项入库
│   ├── hybrid_retriever.py # 混合检索与相关度门槛
│   ├── migration.py    # 显式迁移与索引重建
│   ├── backup.py       # 清单校验、备份与恢复
│   ├── embedder.py     # Embedding 模型
│   ├── retriever.py    # 向量检索
│   ├── generator.py    # LLM 生成
│   └── image_processor.py  # 图片处理
├── data/               # 数据存储
│   ├── raw/            # 原始文档
│   └── domains/        # 向量数据库
├── docs/               # 文档资料
└── tests/              # 测试代码
```

## 🔧 支持的 LLM API

### 小米 MiMo（推荐）

- 官网：https://token-plan-cn.xiaomimimo.com
- 模型：mimo-v2.5-pro、mimo-v2.5、mimo-v2-pro 等

### 硅基流动 SiliconFlow

- 官网：https://cloud.siliconflow.cn
- 模型：XiaomiMiMo/MiMo-7B-RL 等

## 📖 使用指南

详细使用说明请参考 [使用指南.md](使用指南.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
