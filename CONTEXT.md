# Project Context

## Domain Vocabulary

- **RAG**: 先从知识库召回证据，再让生成模型基于证据回答问题的检索增强生成流程。
- **知识库**: 面向一个业务域或客户的文档档案、原始资料、切片和检索索引的完整集合。
- **租户隔离**: 不同客户的数据、索引、原始文件和管理权限在物理或逻辑上隔离，检索必须强制带租户边界。
- **文档档案**: SQLite 中记录文档身份、内容哈希、分类、责任人、版本、更新时间、权限和来源的权威记录。
- **元数据**: 文档及切片的结构化字段；当前核心字段为 `document_id`、`category`、`owner`、`version`、`updated_at`、`permission` 和 `source`。
- **切片（chunk）**: 文档按标题、段落或表格边界拆分后用于独立检索的最小证据单元，必须保留标题上下文和来源位置。
- **混合检索**: 组合语义向量检索与关键词/全文检索，再去重、重排并进行相关性过滤的召回流程。
- **主动版本（active version）**: 同一文档名下默认参与问答的最新有效版本；历史版本保留用于审计和回溯。
- **拒答**: 召回不足、超出知识库范围或证据无法支撑结论时，系统明确说明资料不足，不让模型猜测。
- **引用溯源**: 回答中的每个结论都能关联到文档名称、版本、章节或页码及具体证据片段。
- **增量更新**: 仅处理新增或变更的数据源，按内容哈希去重并让新版本进入索引，避免全量重扫。
- **反馈回流**: 用户标记答案不准确后，将问题、证据和修正结果送入人工确认队列，形成可复用的经验或回归案例。
- **经验卡片**: 经过用户确认、可搜索、可归档的结构化经验记录，独立于原始文档档案和文档索引。
- **索引重建**: 从 SQLite 文档档案或经验卡片重新生成可丢弃的检索投影，不删除原始资料和权威记录。
- **验收门禁**: 发布前统一执行测试、离线评测、编译、恢复演练和进程健康检查的阻断流程。
- **P0/P1/P2**: P0 为必须可运行的基础链路，P1 为投入持续使用所需的质量与运维能力，P2 为后续增强项。

## Key Decisions

- [ADR-0001](docs/adr/0001-local-first-sustainable-edition.md)：当前阶段锁定为单用户本地可持续版；企业级多租户、外部 API 和管理后台暂不进入本阶段核心架构。
- [ADR-0002](docs/adr/0002-local-directory-incremental-sync.md)：本地版先采用本地目录增量扫描，可手动执行并可选接入 Windows Task Scheduler；常驻监听和外部数据源暂不纳入。
- [ADR-0003](docs/adr/0003-preserve-knowledge-on-source-removal.md)：扫描发现源文件删除或改名时保留知识库档案和历史版本，标记源文件缺失并从默认问答排除，物理清理由用户显式确认。
- [ADR-0004](docs/adr/0004-preview-before-sync-apply.md)：目录同步人工执行默认只预览，只有显式 `--apply` 才写入；定时任务也必须显式应用并保留运行锁、日志和逐文件失败隔离。
- [ADR-0005](docs/adr/0005-manual-semantic-index-rebuild.md)：Embedding/语义索引失败时先保留 FTS 可用并标记待重建，不运行后台自动任务；用户可在界面显式点击重建，失败项在下次同步中继续提示。
- [ADR-0006](docs/adr/0006-minimize-cloud-llm-egress.md)：默认可使用配置的云端 LLM，但只发送当前问题和最终 Top-K 证据片段；提供本地隐私模式，不发送云端请求。
- [ADR-0007](docs/adr/0007-defer-authorization-in-local-edition.md)：单用户本地版不实现认证、角色和权限过滤；`permission` 如预留只作描述性元数据，不作为安全边界。
- [ADR-0008](docs/adr/0008-next-slice-local-incremental-sync.md)：下一轮只交付本地目录增量同步纵向切片，覆盖预览、应用、缺失标记、锁、日志和回归测试，不并行扩展外部平台能力。

## Module Map

- `app.py` — Streamlit 用户界面、问答、批量上传、文档浏览、经验库和运维操作入口。
- `core/loader.py` — PDF、DOCX、Markdown、文本等资料解析。
- `core/splitter.py` — 按结构切分文档并保留标题和位置上下文。
- `core/metadata.py` — 元数据字段、默认值和治理校验。
- `core/ingestion.py` — 单文件解析、去重、版本替换、档案写入和索引投影更新。
- `core/document_store.py` — SQLite 文档档案、切片记录和 FTS5 关键词投影。
- `core/retriever.py` — 语义向量检索适配和领域索引访问。
- `core/hybrid_retriever.py` — 关键词与语义结果融合、去重、重排和相关性过滤。
- `core/generator.py` — 基于检索证据生成带引用约束的回答。
- `core/agent.py` — 问答编排、拒答和经验沉淀相关流程。
- `core/experience_store.py` / `core/experience_index.py` / `core/experience_pipeline.py` — 经验卡片存储、相似检索和人工确认流程。
- `core/migration.py` — 既有原始资料迁移和文档索引重建。
- `core/backup.py` — 数据库和原始资料备份、清单校验和恢复。
- `core/observability.py` — 检索、索引健康和运行状态观测。
- `core/evaluation.py` — 脱敏 fixture/live RAG 评测、基线比较和质量退出码。
- `core/acceptance.py` — P1 总验收门禁、安全证据报告和恢复演练。
- `tests/` — 单元测试、回归案例、离线评测 fixture 和验收测试。

## Source Notes

- 参考文档：`D:\TASK\分析报告\知识库\基于RAG知识库的开发文档.docx`，读取时间 2026-08-31。
- 文档审阅限制：本机缺少 LibreOffice/`soffice`，无法完成 DOCX 到 PNG 的视觉渲染；结构化文本、表格、修订标记和评论部件已检查。
