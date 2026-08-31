# P1 本地知识库总验收记录

日期：2026-08-31
提交：`b4c3f78`

## 自动验收结果

运行命令：

```powershell
python -m core.acceptance --project-root . `
  --cases tests/fixtures/evaluation_cases.json `
  --corpus tests/fixtures/evaluation_corpus.json `
  --streamlit-url http://localhost:8502/_stcore/health `
  --json-out data/evaluation/p1-acceptance.json
```

| 检查项 | 结果 | 证据摘要 |
|---|---|---|
| 自动化测试 | passed | 全部测试通过 |
| 公开离线评测 | passed | hit@k、active version、拒答准确率、引用覆盖率均为 1.000 |
| Python 编译 | passed | `compileall` 通过 |
| 依赖检查 | warning | 工作站全局环境存在与项目无关的缺失包，不阻断门禁 |
| 恢复演练 | passed | 临时数据备份、SHA-256 校验、恢复和 FTS 重建通过 |
| Streamlit 健康 | passed | `http://localhost:8502/_stcore/health` 返回 200 |

总状态：`passed`。重试清单为空。

## 手工验收清单

- [x] 批量上传预览、元数据审核和失败项重试
- [x] 迁移现有资料和索引重建
- [x] 无相关资料时拒答
- [x] 回答显示文档版本、章节/页码和来源
- [x] 经验卡片保存、重复审核、归档/恢复
- [x] 索引故障后保留 SQLite 档案并可重建

报告仅记录检查状态、指标摘要和重试清单，不包含问题原文、答案、召回片段、完整本地路径或密钥。
