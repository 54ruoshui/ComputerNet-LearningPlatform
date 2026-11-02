"""
GraphRAG ReAct Agent（基于 LangGraph create_react_agent）

将知识图谱检索、可视化、统计等功能封装为 LangChain Tool，
由 LLM 自主决策调用哪些工具来回答用户问题。
"""

import asyncio
import json
import sqlite3
import uuid
import time
from typing import AsyncGenerator

import structlog
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from neo4j import GraphDatabase

from src.exceptions import ConnectionError_, LLMError
from src.langchain_config import LangGraphRAGConfig
from src.logging_config import session_id_var
from src.embedding_manager import EmbeddingManager
from src.keyword_extractor import KeywordExtractor
from src.services.entity_retriever import EntityRetriever
from src.services.graph_stats_service import GraphStatsService
from src.services.graph_visualization_service import GraphVisualizationService

logger = structlog.get_logger(__name__)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _empty_graph() -> dict:
    return {"nodes": [], "relationships": []}


# 兜底重试的用户消息标记：get_session_history 据此过滤，避免内部重试泄漏为可见用户消息
_FALLBACK_RETRY_MARKER = "请基于上述知识图谱检索上下文，重新回答我刚才的问题。"


# ==================== 工具工厂（闭包绑定 retriever） ====================

def make_tools(entity_retriever: EntityRetriever, stats_service: GraphStatsService) -> list:
    """创建绑定到指定服务的工具实例列表"""

    @tool
    def knowledge_search(query: str) -> str:
        """搜索知识图谱，获取与网络知识相关的实体、关系、问答上下文以及可视化数据。
        当用户询问计算机网络的概念、协议、设备或各层知识时，应优先使用此工具。
        返回结果包含文本上下文和可视化图数据。

        Args:
            query: 搜索问题或关键词，如"TCP三次握手"、"CSMA/CD工作原理"
        """
        try:
            result = entity_retriever.search_with_graph(query)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("knowledge_search 工具调用失败", error=str(e))
            return f"知识图谱检索失败: {e}"

    @tool
    def graph_statistics() -> str:
        """获取知识图谱的统计信息，包括实体总数、关系总数、各类型数量、各层实体数等。
        当用户询问知识图谱的规模、覆盖范围或数据组成时使用此工具。"""
        try:
            stats = stats_service.get_stats()
            return json.dumps(stats, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("graph_statistics 工具调用失败", error=str(e))
            return json.dumps({"error": str(e)})

    @tool
    def node_search(query: str, limit: int = 10) -> str:
        """在知识图谱中按关键词搜索节点，返回匹配的节点名称、类型和描述。
        用于查找特定实体或精确了解某个概念的基本信息。

        Args:
            query: 搜索关键词
            limit: 返回结果数量上限，默认10
        """
        try:
            nodes = entity_retriever.keyword_search(query, limit=limit)
            return json.dumps(nodes, ensure_ascii=False)
        except Exception as e:
            logger.error("node_search 工具调用失败", error=str(e))
            return json.dumps([])

    @tool
    def node_neighbors(node_name: str, depth: int = 2) -> str:
        """获取知识图谱中某个节点的邻居节点，探索实体间的连接关系。
        用于深入了解某个实体周围的相关概念和关联。

        Args:
            node_name: 节点名称（需精确匹配，如"TCP"、"以太网"）
            depth: 探索深度，1=直接邻居，2=两跳邻居。默认2
        """
        try:
            neighbors = entity_retriever.get_neighbors(node_name, depth=depth)
            return json.dumps(neighbors, ensure_ascii=False)
        except Exception as e:
            logger.error("node_neighbors 工具调用失败", node=node_name, error=str(e))
            return json.dumps([])

    return [knowledge_search, graph_statistics, node_search, node_neighbors]


# ==================== Agent 类 ====================

class GraphRAGAgent:
    """基于 LangGraph ReAct Agent 的 GraphRAG 查询引擎，支持多工具调用和会话记忆"""

    def __init__(self, config: LangGraphRAGConfig | None = None):
        self.config = config or LangGraphRAGConfig()

        # Neo4j driver
        try:
            self.driver = GraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password)
            )
        except Exception as e:
            raise ConnectionError_(
                "Neo4j 连接失败",
                detail=f"uri={self.config.neo4j_uri}",
                cause=e,
            ) from e

        # LLM — ZhipuAI via OpenAI-compatible endpoint
        try:
            self.llm = ChatOpenAI(
                base_url=self.config.zhipu_base_url,
                api_key=self.config.zhipuai_api_key,
                model=self.config.zhipu_model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                streaming=True,
                max_retries=6,
            )
        except Exception as e:
            raise LLMError(
                "LLM 初始化失败",
                detail=f"model={self.config.zhipu_model}",
                cause=e,
            ) from e

        # 服务层（直接创建，共享 driver）
        embedding_mgr = EmbeddingManager()
        keyword_extractor = KeywordExtractor()
        self._stats_service = GraphStatsService(driver=self.driver)
        self._viz_service = GraphVisualizationService(
            driver=self.driver,
            entity_retriever_fn=None,  # 将在 EntityRetriever 创建后设置
        )
        self._entity_retriever = EntityRetriever(
            driver=self.driver,
            embedding_mgr=embedding_mgr,
            keyword_extractor=keyword_extractor,
            max_entities=self.config.max_entities,
            viz_service=self._viz_service,
        )
        self._viz_service._retrieve_entities = self._entity_retriever.retrieve_entities

        # 会话记忆（sqlite 持久化 / memory 非持久化）
        if self.config.memory_backend == "sqlite":
            self._db_conn = sqlite3.connect(
                self.config.sqlite_db_path, check_same_thread=False, timeout=30
            )
            self._db_conn.execute("PRAGMA busy_timeout=30000")
            self.checkpointer = SqliteSaver(self._db_conn)
            self.checkpointer.setup()
        else:
            self._db_conn = None
            self.checkpointer = MemorySaver()
        self._sessions: set = set()

        # 构建工具（闭包绑定服务）
        self.tools = make_tools(self._entity_retriever, self._stats_service)

        # 构建 ReAct Agent
        self.agent = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self._build_system_prompt(),
            checkpointer=self.checkpointer,
        )

        logger.info("GraphRAG ReAct Agent 初始化完成")

    # -------------------- 系统提示词 --------------------

    @staticmethod
    def _build_system_prompt() -> str:
        return """你是一个计算机网络领域的资深专家和优秀教师。

# 工具使用
- 凡是涉及计算机网络知识的问题（概念、协议、设备、原理、对比、场景等），**必须先调用 knowledge_search 检索知识图谱，再基于检索结果回答**，不允许仅凭自身知识直接作答。
- 当需要了解图谱规模或统计数据时，调用 graph_statistics。
- 当需要查找特定节点或探索实体关系时，调用 node_search 或 node_neighbors。
- 仅以下情况可以直接回答，不必调用工具：问候、闲聊、对刚才回答内容的追问澄清、以及与计算机网络完全无关的问题。

# 回答要求
1. **详细充分**：回答应当详尽充实，每个知识点充分展开解释
2. **结构清晰**：使用多级标题、编号列表、对比表格组织内容
3. **解释原理**：不仅给出"是什么"，还要解释"为什么"和"怎么工作的"
4. **举例说明**：为核心概念给出具体的、贴近实际的例子
5. **对比分析**：涉及多个概念时，主动进行对比，突出差异和联系
6. **总结归纳**：在回答末尾给出简洁的总结或要点回顾

# 约束
1. 技术问题需基于工具返回的信息回答，信息不足时明确说明
2. 使用中文回答，语言流畅自然，使用 Markdown 格式
3. 结合对话上下文连贯回答"""

    # -------------------- 预检索兜底 --------------------

    @staticmethod
    def _boundary_after(messages: list, message_id: str) -> int:
        """指定 id 消息之后的下标，用于界定本轮新增消息；未找到返回 0（宁可漏判兜底）"""
        for i, m in enumerate(messages):
            if getattr(m, "id", None) == message_id:
                return i + 1
        return 0

    @staticmethod
    def _extract_answer(messages: list) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                return msg.content
        return ""

    @staticmethod
    def _no_tool_calls(new_messages: list) -> bool:
        return not any(
            isinstance(m, AIMessage) and m.tool_calls for m in new_messages
        )

    def _pre_retrieve(self, question: str, session_id: str | None) -> dict | None:
        """模型未发起工具调用时的预检索；无结果（如闲聊）返回 None"""
        try:
            result = self._entity_retriever.search_with_graph(question)
        except Exception as e:
            logger.error("预检索兜底检索失败", error=str(e))
            return None
        context = result.get("context") or ""
        graph_data = result.get("graph_data") or _empty_graph()
        if not (context or graph_data.get("nodes")):
            return None
        logger.warning(
            "模型本轮未发起任何工具调用，已触发预检索兜底",
            model=self.config.zhipu_model,
            session_id=session_id,
        )
        return {"context": context, "graph_data": graph_data}

    def _regenerate_answer(self, context: str, config: dict) -> str:
        """注入检索上下文到当前会话重答；失败或无内容返回空串，调用方保留原回答"""
        try:
            result = self.agent.invoke({"messages": [
                SystemMessage(content=(
                    "上一条回答未经过知识图谱检索。以下是针对用户问题的知识图谱检索结果：\n\n"
                    f"{context}\n\n"
                    "请严格基于上述检索上下文，重新完整回答用户上一条问题，"
                    "不要向用户提及本次重试或检索机制。"
                )),
                HumanMessage(content=_FALLBACK_RETRY_MARKER),
            ]}, config)
            return self._extract_answer(result.get("messages", []))
        except Exception as e:
            logger.error("预检索兜底重答失败，保留原始回答", error=str(e))
            return ""

    # -------------------- 主查询入口 --------------------

    def query(
        self,
        question: str,
        session_id: str | None = None,
        mastery_context: str = "",
    ) -> dict:
        start_time = time.time()

        if not session_id:
            session_id = str(uuid.uuid4())

        session_id_var.set(session_id)
        self._sessions.add(session_id)
        config = {"configurable": {"thread_id": session_id}}

        messages = []
        if mastery_context:
            messages.append(SystemMessage(content=mastery_context))
        question_id = str(uuid.uuid4())
        messages.append(HumanMessage(content=question, id=question_id))

        try:
            result = self.agent.invoke({"messages": messages}, config)
            messages = result.get("messages", [])

            answer = self._extract_answer(messages)
            new_messages = messages[self._boundary_after(messages, question_id):]

            # 从 ToolMessage 提取 graph_data
            graph_data = _empty_graph()
            context_length = 0
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    if msg.name == "knowledge_search":
                        try:
                            parsed = json.loads(msg.content)
                            if isinstance(parsed, dict) and "graph_data" in parsed:
                                gd = parsed["graph_data"]
                                if gd.get("nodes"):
                                    graph_data = gd
                                context_length += len(parsed.get("context", ""))
                            else:
                                context_length += len(msg.content) if msg.content else 0
                        except (json.JSONDecodeError, TypeError):
                            context_length += len(msg.content) if msg.content else 0

            # 兜底：模型整轮未发起任何工具调用（如模型端不返回 tool_calls）时预检索，
            # 避免检索链路静默失效
            retrieval_fallback = False
            if self._no_tool_calls(new_messages):
                fb = self._pre_retrieve(question, session_id)
                if fb:
                    retrieval_fallback = True
                    if fb["graph_data"].get("nodes"):
                        graph_data = fb["graph_data"]
                    if fb["context"]:
                        context_length += len(fb["context"])
                        answer = self._regenerate_answer(fb["context"], config) or answer

            elapsed = time.time() - start_time

            response = {
                "question": question,
                "answer": answer,
                "processing_time": elapsed,
                "graph_data": graph_data,
                "session_id": session_id,
            }
            if context_length > 0:
                response["context_length"] = context_length
            if retrieval_fallback:
                response["retrieval_fallback"] = True

            logger.info("ReAct Agent 查询完成", elapsed=f"{elapsed:.2f}s", session_id=session_id)
            return response

        except Exception as e:
            logger.error("ReAct Agent 查询失败", error=str(e), session_id=session_id)
            return {
                "question": question,
                "answer": f"查询处理失败: {str(e)}",
                "processing_time": time.time() - start_time,
                "graph_data": _empty_graph(),
                "session_id": session_id,
            }

    # -------------------- 流式查询 --------------------

    async def query_stream(
        self,
        question: str,
        session_id: str | None = None,
        mastery_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """流式查询：异步生成器，每步产出 SSE 格式的事件数据"""
        start_time = time.time()

        if not session_id:
            session_id = str(uuid.uuid4())

        session_id_var.set(session_id)
        self._sessions.add(session_id)
        config = {"configurable": {"thread_id": session_id}}

        messages = []
        if mastery_context:
            messages.append(SystemMessage(content=mastery_context))
        messages.append(HumanMessage(content=question))

        try:
            meta: dict = {}
            async for event in self._do_stream(question, messages, config, meta):
                yield event

            elapsed = time.time() - start_time
            done_data = {"session_id": session_id, "elapsed": round(elapsed, 2)}
            if meta.get("retrieval_fallback"):
                done_data["retrieval_fallback"] = True
            yield _sse("done", done_data)

        except Exception as e:
            logger.error("流式查询失败", error=str(e), session_id=session_id)
            yield _sse("error", {"msg": str(e)})

    async def _do_stream(self, question: str, messages: list, config: dict, meta: dict):
        import aiosqlite as _aiosqlite
        _conn = _aiosqlite.connect(self.config.sqlite_db_path, timeout=30)
        _checkpointer = AsyncSqliteSaver(_conn)
        await _checkpointer.setup()
        _agent = create_react_agent(
            model=self.llm, tools=self.tools,
            prompt=self._build_system_prompt(),
            checkpointer=_checkpointer,
        )
        saw_tool = False
        try:
            async for event in _agent.astream_events(
                {"messages": messages}, config, version="v2"
            ):
                kind = event.get("event", "")

                if kind == "on_tool_start":
                    saw_tool = True
                    tool_name = event.get("name", "")
                    yield _sse("status", {"msg": f"检索中：{tool_name}..."})

                elif kind == "on_tool_end":
                    if event.get("name") == "knowledge_search":
                        output = event.get("data", {}).get("output")
                        try:
                            content = output.content if hasattr(output, "content") else str(output)
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and "graph_data" in parsed:
                                gd = parsed["graph_data"]
                                if gd.get("nodes"):
                                    yield _sse("graph", gd)
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield _sse("token", {"content": chunk.content})

            # 兜底：整轮无工具调用时补发预检索结果（token 已流出，无法重答，
            # 但保证图谱面板有数据且失效可被观测）
            if not saw_tool:
                fb = self._pre_retrieve(question, config.get("configurable", {}).get("thread_id"))
                if fb:
                    if fb["graph_data"].get("nodes"):
                        yield _sse("graph", fb["graph_data"])
                    meta["retrieval_fallback"] = True
        finally:
            await _conn.close()

    # -------------------- 会话管理 --------------------

    def get_session_history(self, session_id: str) -> list[dict]:
        config = {"configurable": {"thread_id": session_id}}
        try:
            state = self.agent.get_state(config)
            if state and state.values:
                messages = state.values.get("messages", [])
                history = []
                for msg in messages:
                    if isinstance(msg, HumanMessage) and _FALLBACK_RETRY_MARKER not in msg.content:
                        history.append({"role": "user", "content": msg.content})
                    elif isinstance(msg, AIMessage) and msg.content:
                        history.append({"role": "assistant", "content": msg.content})
                return history
        except Exception as e:
            logger.warning("获取会话历史失败", error=str(e), session_id=session_id)
        return []

    def list_sessions(self) -> list[str]:
        if self._db_conn:
            try:
                # langgraph>=1.0 list() 产出 CheckpointTuple（每个 checkpoint 一条，需按 thread 去重）
                sessions: list[str] = []
                seen: set[str] = set()
                for ckpt in self.checkpointer.list(None):
                    tid = ckpt.config.get("configurable", {}).get("thread_id")
                    if tid and tid not in seen:
                        seen.add(tid)
                        sessions.append(tid)
                return sessions
            except Exception as e:
                logger.warning("从 checkpointer 列出会话失败，回退内存集合", error=str(e))
        return list(self._sessions)

    def new_session(self) -> str:
        session_id = str(uuid.uuid4())
        self._sessions.add(session_id)
        return session_id

    def delete_session(self, session_id: str) -> bool:
        if self._db_conn:
            try:
                self.checkpointer.delete_thread(session_id)
                self._sessions.discard(session_id)
                return True
            except Exception:
                pass
        existed = session_id in self._sessions
        self._sessions.discard(session_id)
        return existed

    # -------------------- 其他接口 --------------------

    def get_stats(self) -> dict:
        return self._stats_service.get_stats()

    @property
    def entity_retriever(self) -> EntityRetriever:
        return self._entity_retriever

    @property
    def stats_service(self) -> GraphStatsService:
        return self._stats_service

    def close(self):
        if self.driver:
            self.driver.close()
        if self._db_conn:
            self._db_conn.close()
            logger.info("已关闭 Neo4j 及 SQLite 连接")
