"""
GraphRAG ReAct Agent（基于 LangGraph create_react_agent）

将知识图谱检索、可视化、统计等功能封装为 LangChain Tool，
由 LLM 自主决策调用哪些工具来回答用户问题。
"""

import json
import uuid
import time

import structlog
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

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
        self._entity_retriever = EntityRetriever(
            driver=self.driver,
            embedding_mgr=embedding_mgr,
            keyword_extractor=keyword_extractor,
            max_entities=self.config.max_entities,
        )
        self._stats_service = GraphStatsService(driver=self.driver)
        self._viz_service = GraphVisualizationService(
            driver=self.driver,
            entity_retriever_fn=self._entity_retriever.retrieve_entities,
        )

        # 会话记忆
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

# 重要：必须调用工具
对于用户的每一个问题，你**必须首先调用 knowledge_search 工具**进行检索，然后根据工具返回的结果来回答。
绝对不要跳过工具调用直接回答问题。即使你认为你知道答案，也必须先调用 knowledge_search。

# 回答要求
1. **详细充分**：回答应当详尽充实，每个知识点充分展开解释，不少于500字
2. **结构清晰**：使用多级标题、编号列表、对比表格组织内容
3. **解释原理**：不仅给出"是什么"，还要解释"为什么"和"怎么工作的"
4. **举例说明**：必须为每个核心概念给出一个具体的、贴近实际的例子
5. **对比分析**：涉及多个概念时，主动进行对比，突出差异和联系
6. **总结归纳**：在回答末尾给出简洁的总结或要点回顾

# 严格约束
1. 必须仅基于工具返回的信息回答，不允许使用外部知识编造内容
2. 如果工具返回信息不足，明确说明缺少哪些信息
3. 使用中文回答，语言流畅自然，使用 Markdown 格式
4. 对于用户之前对话的延续，结合上下文连贯回答"""

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
        messages.append(HumanMessage(content=question))
        input_messages = {"messages": messages}

        try:
            result = self.agent.invoke(input_messages, config)
            messages = result.get("messages", [])

            # 提取最终 AI 回答
            answer = ""
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    answer = msg.content
                    break

            # 从 ToolMessage 提取 graph_data
            graph_data = {"nodes": [], "relationships": []}
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

            logger.info("ReAct Agent 查询完成", elapsed=f"{elapsed:.2f}s", session_id=session_id)
            return response

        except Exception as e:
            logger.error("ReAct Agent 查询失败", error=str(e), session_id=session_id)
            return {
                "question": question,
                "answer": f"查询处理失败: {str(e)}",
                "processing_time": time.time() - start_time,
                "graph_data": {"nodes": [], "relationships": []},
                "session_id": session_id,
            }

    # -------------------- 会话管理 --------------------

    def get_session_history(self, session_id: str) -> list[dict]:
        config = {"configurable": {"thread_id": session_id}}
        try:
            state = self.agent.get_state(config)
            if state and state.values:
                messages = state.values.get("messages", [])
                history = []
                for msg in messages:
                    if isinstance(msg, HumanMessage):
                        history.append({"role": "user", "content": msg.content})
                    elif isinstance(msg, AIMessage) and msg.content:
                        history.append({"role": "assistant", "content": msg.content})
                return history
        except Exception as e:
            logger.warning("获取会话历史失败", error=str(e), session_id=session_id)
        return []

    def list_sessions(self) -> list[str]:
        return list(self._sessions)

    def new_session(self) -> str:
        session_id = str(uuid.uuid4())
        self._sessions.add(session_id)
        return session_id

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            self._sessions.discard(session_id)
            return True
        return False

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
            logger.info("已关闭 Neo4j 连接 (ReAct Agent)")
