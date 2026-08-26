# 离线 RAG 评测与回归报告设计

## 目标

为本地知识库建立一个不依赖云端大模型的、可重复运行的质量评测入口，用于回答三个问题：

1. 检索是否找到了预期的文档和 active 版本；
2. 资料不足的问题是否被正确拒答；
3. 召回结果是否具备生成可追溯引用所需的来源信息。

本设计对应 GitHub Issue #1，服务于 P1 的质量闭环。它不改变现有检索排序、切片或回答逻辑，只提供可验证的外部质量门禁。

## 范围

### 本期包含

- 脱敏公开 fixture 评测集，提交到仓库并可在无真实业务数据的环境运行；
- 本地私有评测集，读取用户本机的真实问题和期望结果，不进入 Git；
- 统一案例格式和严格输入校验；
- 检索命中率、active 版本命中率、拒答准确率、引用覆盖率四项指标；
- 人类可读终端报告和机器可读 JSON 报告；
- 明确的指标阈值、失败案例 ID 和退出码；
- 确定性 fixture 运行方式，不调用 OpenAI 或其他云端模型。

### 本期不包含

- 自动收集真实用户问题或自动将反馈加入评测集；
- 调用 LLM 评判答案质量；
- 自动修改检索权重、阈值、切片或模型配置；
- 将真实问题、原始文档正文或完整引用片段写入公开报告；
- 替代现有 `pytest` 测试或 Streamlit 手工验收。

## 设计原则

- SQLite 文档档案是 active/superseded 状态的事实来源，评测不得绕过版本状态直接读取 Chroma 结果判定通过。
- 公开 fixture 只使用脱敏资料；本地 private 集通过 `.gitignore` 或现有本地数据目录隔离。
- 评测失败必须 fail closed：输入无效、检索器异常、索引不可用或指标低于阈值时返回非零退出码。
- 报告默认只输出案例 ID、指标、失败原因和安全的文档标识，不输出 API Key、完整问题或原文正文。
- 指标计算必须确定性、可解释、可单元测试；同一数据和检索器重复运行应得到相同结果。

## 架构

```text
案例 JSON
   |
   v
案例加载与校验
   |
   v
评测运行器 ----> HybridRetriever / 注入的检索函数
   |
   +--> 每案例结果
   +--> 聚合指标
   +--> 阈值与基线比较
   |
   v
终端报告 + JSON 报告 + 退出码
```

新增 `core.evaluation`，只依赖案例数据和一个可注入的检索器接口，不依赖 Streamlit、OpenAI 客户端或回答生成器。公开 fixture 模式使用临时 SQLite/FTS5 数据和确定性语义回调；live 模式连接当前本地文档库和已存在的语义索引。

## 案例数据格式

评测文件是 UTF-8 JSON，顶层必须是对象：

```json
{
  "version": 1,
  "thresholds": {
    "hit_at_k": 1.0,
    "active_version_hit_rate": 1.0,
    "refusal_accuracy": 1.0,
    "citation_coverage": 1.0
  },
  "cases": [
    {
      "id": "fixture-001",
      "question": "报销制度的生效日期是什么？",
      "domain": "制度",
      "expected_documents": ["报销制度.md"],
      "expected_versions": ["2"],
      "expected_sections": ["生效日期"],
      "expected_pages": [],
      "required_terms": ["2026"],
      "should_refuse": false
    }
  ]
}
```

字段规则：

- `version` 必须为正整数，当前只接受 `1`；未知 schema 版本直接失败；
- `thresholds` 的四项值必须是 `0.0` 到 `1.0` 的数字；缺失时使用公开 fixture 默认值，但 live 模式要求显式提供阈值或已存在的本地基线；
- `cases` 必须非空，案例 `id` 在同一文件内唯一且只能包含字母、数字、点、下划线和短横线；
- `question`、`domain` 非空；`expected_documents`、`expected_versions`、`expected_sections`、`expected_pages`、`required_terms` 都必须是数组；
- `should_refuse=true` 时，所有 expected 数组必须为空；
- `should_refuse=false` 时，至少提供一个 `expected_documents` 项；
- `expected_pages` 只能包含正整数；其他字符串数组不得包含空字符串；
- 不允许在案例中保存原始长正文、API Key、模型响应或未经脱敏的敏感信息。校验器对单字段长度设置上限，超限直接失败。

公开 fixture 至少包含 20 个案例，覆盖：

1. 普通标题/章节召回；
2. 编号查询；
3. 日期查询；
4. FAQ 一问一答；
5. 表格内容；
6. 同名文档版本替换；
7. 页码引用；
8. 不相关问题拒答。

## 评测指标

每个案例取 `top_k` 召回结果，默认使用 5；评测器只消费 `RetrievedChunk` 等具有文档名、版本、章节、页码和正文元数据的结果。

### Hit@K

非拒答案例只要 Top-K 中存在同时满足以下条件的结果即命中：

- `document_name` 命中 `expected_documents` 之一；
- 如果提供 `expected_sections`，章节标题或标题路径包含任一预期章节；
- 如果提供 `expected_pages`，页码命中任一预期页码；
- 如果提供 `required_terms`，至少一个结果正文包含全部 required terms。

Hit@K = 命中案例数 / 非拒答案例数。

### Active 版本命中率

非拒答案例只要命中结果的 `document_version` 属于 `expected_versions` 即视为版本命中。若案例未提供 expected version，则该案例不参与该项分母。

Active 版本命中率 = 预期版本命中案例数 / 提供 expected_versions 的非拒答案例数。

评测器额外检查召回结果中不能出现同一文档的 `superseded` 版本；出现即记录 `inactive_version_recalled` 失败。

### 拒答准确率

- `should_refuse=true`：检索结果必须为空；
- `should_refuse=false`：检索结果不得为空且必须满足 Hit@K 条件。

拒答准确率 = 拒答判断正确的案例数 / 全部案例数。

该指标只评估检索层拒答，不调用生成器，也不把 LLM 文案作为判定依据。

### 引用覆盖率

非拒答案例的第一条有效召回结果必须具备：

- 非空文档名称；
- 非空文档版本；
- 非空章节标题或页码至少一项；
- 非空正文片段。

引用覆盖率 = 满足以上字段要求的非拒答案例数 / 非拒答案例数。

## 基线与阈值

- 公开 fixture 的阈值写入案例文件，默认四项均为 `1.0`；任何指标低于阈值时命令返回退出码 `1`。
- live 模式读取用户指定的私有案例文件和同目录的基线 JSON；首次运行可使用 `--accept-baseline` 明确创建基线，后续运行不得自动覆盖。
- 基线包含案例文件 SHA-256、评测器版本、四项聚合指标和生成时间；案例文件发生变化时必须重新明确接受基线。
- live 模式比较当前指标与基线，任何指标下降超过 `0.0001` 即失败；当前基线不存在、无法解析或案例 hash 不一致时返回退出码 `2`，表示配置/运行失败而非质量通过。
- 报告输出每项当前值、目标阈值或基线值、差值和是否通过，不输出完整问题或正文。

## 命令行接口

公开 fixture：

```powershell
python -m core.evaluation --mode fixture --cases tests/fixtures/evaluation_cases.json
```

本地私有集：

```powershell
python -m core.evaluation --mode live --cases data/evaluation/private_cases.json
```

明确接受新的本地基线：

```powershell
python -m core.evaluation --mode live --cases data/evaluation/private_cases.json --accept-baseline
```

可选参数：`--top-k` 覆盖每案例召回数量，`--json-out` 写入机器可读报告，`--baseline` 指定基线路径。默认 JSON 输出到标准输出时只包含安全聚合信息和案例 ID。

运行前校验：

1. `--mode fixture` 必须使用仓库内公开案例；
2. `--mode live` 必须确认数据库和语义索引可读，不允许用空检索器替代；
3. 两种模式都必须在执行任何案例前完成 schema 校验；
4. 评测运行期间不创建 OpenAI client，不读取或发送 API Key。

## 失败语义

| 情况 | 退出码 | 报告状态 |
|---|---:|---|
| 所有案例通过阈值/基线 | 0 | passed |
| 案例执行完成但指标不达标 | 1 | failed |
| JSON、阈值、基线或索引配置无效 | 2 | error |
| 未捕获的程序异常 | 3 | error |

单个案例的检索异常不能被吞掉：记录案例 ID 和安全错误类型，整次运行状态为 `error`，不输出错误堆栈中的问题正文或路径中的敏感信息。

## 测试策略

自动化测试覆盖：

- 合法案例文件加载和完整 schema 校验；
- 重复 ID、缺失字段、错误类型、空数组冲突和超长字段被拒绝；
- Hit@K 命中、章节/页码/关键词匹配和版本匹配；
- superseded 版本召回被识别为失败；
- should_refuse 的空结果通过，非空结果失败；
- 引用字段完整和缺失分支；
- 阈值失败、基线下降和案例 hash 变化；
- fixture 模式不构造 OpenAI client，且报告不包含原文；
- CLI 退出码和 JSON 报告结构。

验收时必须提供：

1. 至少 20 条公开脱敏 fixture 全量通过；
2. 同一命令重复运行得到相同聚合指标；
3. live 私有集能够创建并比较基线，且私有内容不会被写入 Git 跟踪文件；
4. `pytest -v`、`compileall`、`pip check` 和 `git diff --check` 全部通过。

## 后续扩展边界

真实用户反馈转评测案例、答案质量人工标注、Rerank 对比、查询路由和双模型复核属于 P2，不在本 Issue 中实现。评测模块保留注入式检索接口，使这些后续实验可以复用同一指标和报告格式。

