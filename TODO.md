# GraphRAG 工程化待办清单

## 已完成

- [x] **统一配置管理** — pydantic-settings，必填字段校验
- [x] **删除硬编码密码** — 清除 config/ 目录，统一走 .env
- [x] **API Key 认证中间件** — web/middleware.py，X-API-Key header
- [x] **输入校验** — Pydantic Field 限制长度
- [x] **自定义异常体系** — src/exceptions.py
- [x] **结构化日志** — structlog + request_id + session_id
- [x] **外部服务重试** — tenacity（Neo4j/LLM/Embedding）
- [x] **全局异常处理** — FastAPI exception_handler
- [x] **Agent 工具调用修复** — system prompt 强制调用 knowledge_search

---

## 第三阶段：测试（优先级高） ✅

- [x] 搭建 pytest 框架，创建 `tests/` 目录
- [x] 单元测试：`src/embedding_manager.py`（mock DashScope API）
- [x] 单元测试：`src/langchain_retriever.py`（mock Neo4j driver）
- [x] 单元测试：`src/graphrag_agent.py`（mock LLM，验证工具调用）
- [x] 单元测试：`src/settings.py`（必填字段缺失、校验规则）
- [x] 单元测试：`web/middleware.py`（API Key 白名单/拦截）
- [x] 集成测试：FastAPI 端到端（httpx + pytest-asyncio）
- [x] 在 GitHub Actions 或本地 CI 中集成测试运行

## 第四阶段：架构拆分 ✅

- [x] `Neo4jGraphRetriever` 拆分为：
  - `EntityRetriever`（语义+关键词检索）
  - `GraphStatsService`（统计）
  - `GraphVisualizationService`（可视化数据）
- [x] 干掉全局变量 `_retriever`，改为依赖注入或类属性
- [x] `web/graph_rag_web.py` 拆分：抽取 service 层，隔离业务逻辑和路由
- [x] `langchain_retriever.py` 里的 LLM 关键词提取单独抽出（当前 with_structured_output 和 glm-4-flash 不兼容，返回长文本而非 JSON）

## 第五阶段：DevOps

- [ ] `requirements.txt` → `pyproject.toml`（用 uv 或 poetry）
- [ ] 编写 Dockerfile + docker-compose.yml（Neo4j + App 一键启动）
- [ ] 依赖版本锁定，加 `pip-audit` 安全扫描
- [ ] `.env.example` 模板文件（不含真实密钥）

## 已知问题

- [x] ~~`glm-4-flash` 的 `with_structured_output` 返回长文本而非 JSON~~ — 已用 `KeywordExtractor` 替换，改为 prompt + regex 解析
- [ ] Neo4j 向量索引 API `db.index.vector.queryNodes` 已被标记 deprecated，需迁移到 `db.index.vector.search`
- [ ] 系统代理会导致 Embedding API（DashScope）和 LLM 关键词提取失败，需在文档中说明或代码中处理 `NO_PROXY`
- [x] ~~`Neo4jGraphRetriever` 中 `get_graph_data_for_visualization` 和 `_build_graph_data` 逻辑高度重复~~ — 已在 `GraphVisualizationService` 中合并
- [ ] `quality_monitor.py` 中的 `main()` 函数里直接读取环境变量，未通过 settings 获取
