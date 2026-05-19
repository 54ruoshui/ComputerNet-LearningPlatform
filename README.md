# GraphRAG 计算机网络知识图谱问答系统

基于 Neo4j 图数据库和智谱AI大模型的计算机网络知识问答系统，采用 FastAPI + LangGraph + LangChain 架构，支持混合语义+关键词检索（GraphRAG）、会话记忆和质量监控。

## 项目简介

这是一个面向计算机网络领域的智能知识问答系统，具有以下核心特性：

- **知识图谱存储**：使用 Neo4j 图数据库按 5 层 OSI 模型分层存储网络协议、设备、概念等知识
- **智能问答**：LangGraph ReAct Agent 自主选择工具（知识搜索、图可视化、统计、节点搜索、邻居探索）完成问答
- **混合检索**：结合 Qwen 语义向量检索与关键词检索，提供精准的知识召回
- **会话记忆**：基于 LangGraph MemorySaver 实现多轮对话上下文记忆
- **质量监控**：实时监控和评估知识质量，支持告警和趋势分析
- **Web 界面**：交互式可视化界面，支持 D3.js 图可视化、语音输入和会话管理

## 功能特性

### 核心功能

| 功能 | 描述 |
|------|------|
| 智能问答 | LangGraph ReAct Agent 自主调用工具完成问答 |
| 混合检索 | Qwen 语义向量检索 + 关键词检索，合并排序返回 |
| 会话记忆 | 多轮对话上下文保持，支持会话创建、切换和删除 |
| 质量监控 | 实时监控提取准确率、验证通过率等指标，支持告警和趋势 |
| 图可视化 | D3.js 交互式图可视化，支持节点搜索和邻居探索 |
| 图谱导出 | Graphviz 生成知识图谱可视化图片（PNG/PDF） |
| 语音输入 | 支持语音输入问题 |
| 历史记录 | 保存查询历史，支持导出 |

## 技术栈

- **后端框架**: FastAPI + Uvicorn
- **AI 框架**: LangGraph + LangChain
- **图数据库**: Neo4j
- **LLM**: 智谱AI GLM-4-Flash（通过 ZhipuAI API）
- **Embeddings**: Qwen text-embedding-v3（通过 DashScope API，1024 维）
- **数据验证**: Pydantic
- **模板引擎**: Jinja2
- **前端**: HTML5, CSS3, JavaScript, D3.js

## 快速开始

### 环境要求

- Python 3.9+
- Neo4j 4.4+（需要 APOC 和向量索引支持）
- 智谱AI API Key
- 通义千问（Qwen）API Key（用于 Embeddings）

### 安装步骤

1. **克隆项目**
   ```bash
   git clone <repository-url>
   cd aigc
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **获取 API Key**

   - **智谱AI**: 访问 [智谱AI开放平台](https://open.bigmodel.cn) 注册并获取 API Key
   - **通义千问**: 访问 [阿里云 DashScope](https://dashscope.console.aliyun.com/) 注册并获取 API Key（用于 Embeddings）

4. **配置环境变量**

   创建 `.env` 文件并配置以下内容：
   ```bash
   # Neo4j 配置
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=your-password

   # 智谱AI LLM 配置
   ZHIPUAI_API_KEY=sk-your-api-key
   ZHIPUAI_MODEL=glm-4-flash

   # Qwen Embeddings 配置（语义检索必需）
   QWEN_API_KEY=sk-your-api-key
   QWEN_EMBEDDING_MODEL=text-embedding-v3

   # Web 配置
   WEB_HOST=0.0.0.0
   WEB_PORT=5000
   DEBUG=false
   ```

5. **启动 Neo4j 数据库**

   确保 Neo4j 服务正在运行，可以通过 http://localhost:7474 访问管理界面

6. **导入知识数据**

   ```bash
   # 从 JSON 文件导入分层知识图谱
   python scripts/build_from_json.py

   # 或从 PDF 文档构建（Microsoft GraphRAG 流水线）
   python scripts/build_index_from_docs.py

   # 生成向量嵌入（语义检索必需）
   python scripts/generate_embeddings.py
   ```

7. **启动 Web 应用**
   ```bash
   python run.py --mode web
   # 或指定 host/port
   python run.py --mode web --host 0.0.0.0 --port 5000 --debug
   ```

8. **访问应用**

   打开浏览器访问 http://localhost:5000

## 使用说明

### Web 界面使用

1. **智能问答**
   - 在输入框中输入关于计算机网络的问题
   - 系统会检索相关知识并生成答案
   - 可查看相关的图结构可视化

2. **图探索**
   - 使用搜索功能查找特定节点
   - 点击节点查看其邻居关系
   - 导出感兴趣的子图数据

### 命令行使用

```bash
# 启动命令行交互界面
python run.py --mode cli

# 运行测试
python run.py --mode test
```

## 项目结构

```
aigc/
├── config/                 # 配置模块
│   └── config.py           # 系统配置
├── scripts/                # 数据导入脚本
│   ├── build_from_json.py              # 从 JSON 导入分层知识图谱
│   ├── build_index_from_docs.py        # 从 PDF 构建（GraphRAG 流水线）
│   └── generate_embeddings.py          # 生成 Qwen 向量嵌入
├── src/                    # 核心源代码
│   ├── graphrag_agent.py               # LangGraph ReAct Agent + 工具 + 会话记忆
│   ├── langchain_retriever.py          # Neo4j 混合检索器（语义 + 关键词）
│   ├── langchain_config.py             # LangChain/LangGraph 配置
│   ├── embedding_manager.py            # Qwen Embedding 管理（DashScope API）
│   ├── enhanced_prompt_engineering.py  # 增强提示词工程
│   ├── quality_monitor.py              # 质量监控与告警
│   └── image_generator.py              # Graphviz 图谱图片生成
├── web/
│   └── graph_rag_web.py                # FastAPI Web 应用
├── templates/
│   └── integrated_index.html           # 主页模板
├── static/js/
│   ├── integrated-app.js               # 前端应用逻辑
│   └── neo4j-graph.js                  # D3.js 图可视化
├── data/
│   ├── layers/             # 分层知识数据 JSON
│   │   ├── 1_physical.json
│   │   ├── 2_data_link.json
│   │   ├── 3_network.json
│   │   ├── 4_transport.json
│   │   ├── 5_application.json
│   │   └── cross_layer.json
│   └── generated_images/   # Graphviz 生成的图片
├── run.py                  # 主启动脚本
└── requirements.txt        # Python 依赖
```

## API 端点

### 问答与会话

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/query` | POST | 主查询接口（`{question, session_id?}`） |
| `/api/sessions` | GET | 列出所有会话 |
| `/api/sessions/new` | POST | 创建新会话 |
| `/api/sessions/{id}/history` | GET | 获取会话聊天历史 |
| `/api/sessions/{id}` | DELETE | 删除会话 |

### 图谱操作

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/graph_stats` | GET | 获取图统计信息 |
| `/api/search_nodes` | GET | 搜索节点（`?q=keyword`） |
| `/api/node_neighbors/{name}` | GET | 获取节点邻居 |
| `/api/export_graph` | POST | 导出子图数据 |
| `/api/generate_image` | POST | 生成 Graphviz 图谱图片 |

### 质量监控

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/quality_report` | GET | 获取质量报告 |
| `/api/quality_alerts` | GET | 获取质量告警 |
| `/api/quality_trends` | GET | 获取质量趋势 |

### 系统

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/config` | GET | 获取功能开关和限制配置 |
| `/api/health` | GET | 健康检查 |
| `/docs` | GET | Swagger API 文档 |

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `NEO4J_URI` | Neo4j 连接地址 | bolt://localhost:7687 |
| `NEO4J_USER` | Neo4j 用户名 | neo4j |
| `NEO4J_PASSWORD` | Neo4j 密码 | - |
| `ZHIPUAI_API_KEY` | 智谱AI API Key（LLM） | - |
| `ZHIPUAI_MODEL` | 智谱AI 模型 | glm-4-flash |
| `QWEN_API_KEY` | 通义千问 API Key（Embeddings） | - |
| `QWEN_EMBEDDING_MODEL` | Qwen Embedding 模型 | text-embedding-v3 |
| `WEB_HOST` | Web 服务器地址 | 0.0.0.0 |
| `WEB_PORT` | Web 服务器端口 | 5000 |
| `DEBUG` | 调试模式 | false |

## 系统架构

```
用户提问
    │
    ▼
FastAPI (/api/query)  ←──  web/graph_rag_web.py
    │
    ▼
GraphRAGAgent  ←──  src/graphrag_agent.py
  LangGraph ReAct Agent + MemorySaver 会话记忆
  LLM (ZhipuAI) 自主选择工具:
    ├── knowledge_search   → 混合语义+关键词检索
    ├── graph_visualize    → 图可视化数据
    ├── graph_statistics   → 图统计信息
    ├── node_search        → 关键词节点搜索
    └── node_neighbors     → 节点邻居探索
    │
    ▼
Neo4jGraphRetriever  ←──  src/langchain_retriever.py
  混合检索:
    1. _semantic_search()  → Qwen Embedding + Neo4j 向量索引
    2. _keyword_search()   → 关键词提取 + Cypher CONTAINS
    3. 合并排序 → _build_context() 返回结构化上下文
    │
    ▼
Neo4j 图数据库
  节点标签: Layer, Entity (Protocol/Device/Concept), Question, Answer
  向量索引: entity_embedding_index (cosine, 1024-dim)
```

## 常见问题

### Neo4j 连接失败

1. 检查 Neo4j 服务是否运行
2. 验证连接地址和端口是否正确
3. 确认用户名和密码是否正确

### 智谱AI API 调用失败

1. 检查 API Key 是否正确配置
2. 确认账户余额是否充足
3. 检查网络连接是否正常

### 语义搜索无结果

1. 确认已运行 `python scripts/generate_embeddings.py` 生成向量嵌入
2. 检查 QWEN_API_KEY 是否正确配置
3. 确认 Neo4j 中 `entity_embedding_index` 向量索引已创建

## 许可证

本项目仅供学习和研究使用。

## 贡献

欢迎提交 Issue 和 Pull Request！
