# 🧠 个人知识库系统

一个基于 RAG（检索增强生成）的个人知识库系统，支持多领域管理、多格式文档上传和智能问答。

## ✨ 功能特性

- 📂 **多领域管理** — 创建和管理多个知识领域
- 📥 **多格式上传** — 支持 PDF、Word、PPT、Markdown、TXT、图片
- 💬 **智能问答** — 基于 RAG 的语义检索和智能回答
- 📚 **文档浏览** — 查看领域内已有的文档列表
- 🔍 **语义搜索** — 基于向量数据库的相似度检索

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

## 📁 项目结构

```
个人知识库系统/
├── app.py              # Streamlit 主入口
├── config.py           # 全局配置
├── requirements.txt    # 依赖列表
├── .env.example        # 环境变量模板
├── core/               # 核心模块
│   ├── loader.py       # 文档加载器
│   ├── splitter.py     # 文本切分
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
