"""GraphRAGAgent 单元测试 — mock LLM，验证工具调用和会话管理"""

import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, ToolMessage

from src.graphrag_agent import _FALLBACK_RETRY_MARKER


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """把 SSE 文本解析成 (event, payload) 列表，供流式断言使用"""
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        evt = next((l[len("event: "):] for l in lines if l.startswith("event: ")), None)
        data = next((l[len("data: "):] for l in lines if l.startswith("data: ")), None)
        if evt:
            events.append((evt, json.loads(data) if data else None))
    return events

from src.exceptions import ConnectionError_, LLMError


# ---------- Fixtures ----------

@pytest.fixture
def mock_entity_retriever():
    er = MagicMock()
    er.search_with_graph.return_value = {
        "context": "TCP是传输控制协议",
        "graph_data": {"nodes": [], "relationships": []},
    }
    er.keyword_search.return_value = [
        {"name": "TCP", "entity_type": "Protocol", "description": "传输控制协议"}
    ]
    er.get_neighbors.return_value = [
        {"name": "IP", "type": "Protocol", "distance": 1}
    ]
    return er


@pytest.fixture
def mock_stats_service():
    ss = MagicMock()
    ss.get_stats.return_value = {
        "entity_count": 100,
        "relationship_count": 200,
    }
    return ss


@pytest.fixture
def agent_with_mocks(mock_entity_retriever, mock_stats_service):
    """创建 GraphRAGAgent，mock 所有外部依赖"""
    with patch("src.graphrag_agent.GraphDatabase") as mock_gdb, \
         patch("src.graphrag_agent.ChatOpenAI") as mock_llm_cls, \
         patch("src.graphrag_agent.create_react_agent") as mock_create, \
         patch("src.graphrag_agent.LangGraphRAGConfig") as mock_config_cls, \
         patch("src.graphrag_agent.EmbeddingManager"), \
         patch("src.graphrag_agent.KeywordExtractor"), \
         patch("src.graphrag_agent.EntityRetriever", return_value=mock_entity_retriever), \
         patch("src.graphrag_agent.GraphStatsService", return_value=mock_stats_service), \
         patch("src.graphrag_agent.GraphVisualizationService"):

        config = MagicMock()
        config.neo4j_uri = "bolt://localhost:7687"
        config.neo4j_user = "neo4j"
        config.neo4j_password = "pw"
        config.zhipu_base_url = "http://test"
        config.zhipuai_api_key = "key"
        config.zhipu_model = "test-model"
        config.temperature = 0.3
        config.max_tokens = 4096
        config.max_entities = 20
        config.max_context_tokens = 8000
        mock_config_cls.return_value = config

        driver = MagicMock()
        mock_gdb.driver.return_value = driver

        mock_agent = MagicMock()
        mock_create.return_value = mock_agent

        from src.graphrag_agent import GraphRAGAgent, make_tools
        agent = GraphRAGAgent.__new__(GraphRAGAgent)
        agent.config = config
        agent.driver = driver
        agent.llm = MagicMock()
        agent._entity_retriever = mock_entity_retriever
        agent._stats_service = mock_stats_service
        agent.checkpointer = MagicMock()
        agent._db_conn = None
        agent._sessions = {"existing-session"}
        agent.tools = make_tools(mock_entity_retriever, mock_stats_service)
        agent.agent = mock_agent

        return agent


def _get_tools(mock_entity_retriever, mock_stats_service):
    """创建绑定到 mock 服务的工具列表"""
    from src.graphrag_agent import make_tools
    return make_tools(mock_entity_retriever, mock_stats_service)


def _find_tool(tools, name):
    """按名称查找工具"""
    for t in tools:
        if t.name == name:
            return t
    raise ValueError(f"Tool {name} not found")


# ---------- 工具函数测试 ----------

class TestKnowledgeSearchTool:

    def test_returns_json_result(self, mock_entity_retriever, mock_stats_service):
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        result = _find_tool(tools, "knowledge_search").invoke({"query": "TCP"})
        parsed = json.loads(result)
        assert "context" in parsed

    def test_handles_exception(self, mock_entity_retriever, mock_stats_service):
        mock_entity_retriever.search_with_graph.side_effect = Exception("db error")
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        result = _find_tool(tools, "knowledge_search").invoke({"query": "TCP"})
        assert "检索失败" in result


class TestGraphStatisticsTool:

    def test_returns_stats(self, mock_entity_retriever, mock_stats_service):
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        result = json.loads(_find_tool(tools, "graph_statistics").invoke({}))
        assert result["entity_count"] == 100


class TestNodeSearchTool:

    def test_returns_nodes(self, mock_entity_retriever, mock_stats_service):
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        result = json.loads(_find_tool(tools, "node_search").invoke({"query": "TCP"}))
        assert len(result) == 1


class TestNodeNeighborsTool:

    def test_returns_neighbors(self, mock_entity_retriever, mock_stats_service):
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        result = json.loads(_find_tool(tools, "node_neighbors").invoke({"node_name": "TCP"}))
        assert len(result) == 1


# ---------- 会话管理 ----------

class TestSessionManagement:

    def test_list_sessions(self, agent_with_mocks):
        sessions = agent_with_mocks.list_sessions()
        assert "existing-session" in sessions

    def test_new_session(self, agent_with_mocks):
        sid = agent_with_mocks.new_session()
        assert sid in agent_with_mocks._sessions

    def test_delete_existing_session(self, agent_with_mocks):
        result = agent_with_mocks.delete_session("existing-session")
        assert result is True
        assert "existing-session" not in agent_with_mocks._sessions

    def test_delete_nonexistent_session(self, agent_with_mocks):
        result = agent_with_mocks.delete_session("no-such-session")
        assert result is False


# ---------- query ----------

class TestQuery:

    def test_successful_query(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.return_value = {
            "messages": [
                HumanMessage(content="什么是TCP?"),
                AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": "TCP"}, "id": "tc1"}]),
                ToolMessage(content=json.dumps({"context": "TCP", "graph_data": {"nodes": [], "relationships": []}}), name="knowledge_search", tool_call_id="tc1"),
                AIMessage(content="TCP是传输控制协议"),
            ]
        }

        result = agent.query("什么是TCP?", session_id="test-session")

        assert result["question"] == "什么是TCP?"
        assert result["answer"] == "TCP是传输控制协议"
        assert result["session_id"] == "test-session"
        assert result["processing_time"] >= 0

    def test_generates_session_id(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.return_value = {"messages": [AIMessage(content="test")]}
        # 非领域问题：预检索无结果，不触发兜底重答
        agent._entity_retriever.search_with_graph.return_value = {"context": "", "graph_data": {"nodes": [], "relationships": []}}

        result = agent.query("test")
        assert result["session_id"] is not None
        assert len(result["session_id"]) > 0

    def test_handles_agent_error(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.side_effect = Exception("LLM error")

        result = agent.query("test", session_id="s1")

        assert "查询处理失败" in result["answer"]
        assert result["session_id"] == "s1"

    def test_extracts_graph_data(self, agent_with_mocks):
        agent = agent_with_mocks
        graph = {"nodes": [{"name": "TCP"}], "relationships": [{"start": {"name": "TCP"}, "end": {"name": "IP"}, "type": "DEPENDS_ON"}]}
        agent.agent.invoke.return_value = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": "TCP"}, "id": "tc1"}]),
                ToolMessage(
                    content=json.dumps({"context": "TCP info", "graph_data": graph}),
                    name="knowledge_search",
                    tool_call_id="tc1",
                ),
                AIMessage(content="TCP answer"),
            ]
        }

        result = agent.query("TCP", session_id="s1")
        assert len(result["graph_data"]["nodes"]) == 1

    def test_mastery_context_included(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.return_value = {"messages": [AIMessage(content="answer")]}
        # 不触发兜底，保证 call_args 是首轮调用
        agent._entity_retriever.search_with_graph.return_value = {"context": "", "graph_data": {"nodes": [], "relationships": []}}

        agent.query("test", session_id="s1", mastery_context="你已掌握TCP")

        call_args = agent.agent.invoke.call_args
        messages = call_args[0][0]["messages"]
        assert any("掌握" in str(m.content) for m in messages)


# ---------- 预检索兜底（模型未发起工具调用时） ----------

class TestRetrievalFallback:
    """docs/已知问题-ReAct工具调用失效.md 的兜底防线"""

    def test_fallback_regenerates_answer_with_context(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.side_effect = [
            {"messages": [HumanMessage(content="VLAN是什么？"), AIMessage(content="未检索的直答")]},
            {"messages": [AIMessage(content="基于图谱的回答")]},
        ]
        agent._entity_retriever.search_with_graph.return_value = {
            "context": "VLAN 是虚拟局域网技术……",
            "graph_data": {"nodes": [{"name": "VLAN"}], "relationships": []},
        }

        result = agent.query("VLAN是什么？", session_id="s1")

        assert result["answer"] == "基于图谱的回答"
        assert result["retrieval_fallback"] is True
        assert len(result["graph_data"]["nodes"]) == 1
        assert agent.agent.invoke.call_count == 2
        retry_messages = agent.agent.invoke.call_args_list[1][0][0]["messages"]
        assert any("检索" in str(m.content) for m in retry_messages)

    def test_no_fallback_when_tools_used(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.return_value = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": "TCP"}, "id": "tc1"}]),
                ToolMessage(content=json.dumps({"context": "TCP", "graph_data": {"nodes": [], "relationships": []}}), name="knowledge_search", tool_call_id="tc1"),
                AIMessage(content="TCP answer"),
            ]
        }

        result = agent.query("TCP", session_id="s1")

        assert "retrieval_fallback" not in result
        agent._entity_retriever.search_with_graph.assert_not_called()

    def test_fallback_skipped_when_retrieval_empty(self, agent_with_mocks):
        """闲聊等场景：模型未调用工具但预检索也无结果，不重答"""
        agent = agent_with_mocks
        agent.agent.invoke.return_value = {"messages": [AIMessage(content="你好，有什么可以帮你？")]}
        agent._entity_retriever.search_with_graph.return_value = {"context": "", "graph_data": {"nodes": [], "relationships": []}}

        result = agent.query("你好", session_id="s1")

        assert result["answer"] == "你好，有什么可以帮你？"
        assert "retrieval_fallback" not in result
        assert agent.agent.invoke.call_count == 1

    def test_fallback_keeps_original_answer_on_retry_error(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.invoke.side_effect = [
            {"messages": [AIMessage(content="直答")]},
            Exception("retry boom"),
        ]
        agent._entity_retriever.search_with_graph.return_value = {
            "context": "some context",
            "graph_data": {"nodes": [], "relationships": []},
        }

        result = agent.query("TCP", session_id="s1")

        assert result["answer"] == "直答"
        assert result["retrieval_fallback"] is True

    def test_fallback_only_checks_current_turn_messages(self, agent_with_mocks):
        """上一轮调用过工具不算数：按输入消息 id 定界，只检查本轮新增消息"""
        agent = agent_with_mocks
        history = [
            HumanMessage(content="Q1"),
            AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": "x"}, "id": "h1"}]),
            ToolMessage(content="{}", name="knowledge_search", tool_call_id="h1"),
            AIMessage(content="A1"),
        ]

        def fake_invoke(inp, cfg):
            # 模拟 checkpointer：历史 + 本轮输入（含带 id 的 HumanMessage）+ 新回答
            return {"messages": history + list(inp["messages"]) + [AIMessage(content="直答")]}

        agent.agent.invoke.side_effect = fake_invoke
        agent._entity_retriever.search_with_graph.return_value = {"context": "", "graph_data": {"nodes": [], "relationships": []}}

        agent.query("Q2", session_id="s1")

        agent._entity_retriever.search_with_graph.assert_called_once()

    def test_fallback_keeps_original_when_retry_answer_empty(self, agent_with_mocks):
        """重答未产出 AI 内容时保留原始回答，标记仍置位"""
        agent = agent_with_mocks
        agent.agent.invoke.side_effect = [
            {"messages": [AIMessage(content="直答")]},
            {"messages": [ToolMessage(content="x", name="t", tool_call_id="t")]},
        ]
        agent._entity_retriever.search_with_graph.return_value = {
            "context": "some context",
            "graph_data": {"nodes": [], "relationships": []},
        }

        result = agent.query("TCP", session_id="s1")

        assert result["answer"] == "直答"
        assert result["retrieval_fallback"] is True

    def test_session_history_hides_fallback_retry_message(self, agent_with_mocks):
        """兜底重试的内部消息不应作为用户消息出现在会话历史里"""
        agent = agent_with_mocks
        state = MagicMock()
        state.values = {"messages": [
            HumanMessage(content="VLAN是什么"),
            HumanMessage(content=_FALLBACK_RETRY_MARKER),
            AIMessage(content="回答"),
        ]}
        agent.agent.get_state.return_value = state

        history = agent.get_session_history("s1")

        contents = [h["content"] for h in history]
        assert "VLAN是什么" in contents
        assert all(_FALLBACK_RETRY_MARKER not in c for c in contents)

    @pytest.mark.asyncio
    async def test_stream_appends_pre_retrieved_graph(self, agent_with_mocks):
        """流式路径：模型直答未走工具时，结束后补发预检索 graph 事件并在 done 标记"""
        agent = agent_with_mocks
        agent._entity_retriever.search_with_graph.return_value = {
            "context": "VLAN 上下文",
            "graph_data": {"nodes": [{"name": "VLAN"}], "relationships": []},
        }

        async def fake_events(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="直答")}}

        fake_agent = MagicMock()
        fake_agent.astream_events = fake_events

        with patch("src.graphrag_agent.create_react_agent", return_value=fake_agent), \
             patch("src.graphrag_agent.AsyncSqliteSaver") as saver_cls, \
             patch("aiosqlite.connect", return_value=AsyncMock()):
            saver_cls.return_value.setup = AsyncMock()
            events = [e async for e in agent.query_stream("VLAN是什么？", session_id="s1")]

        parsed = _parse_sse("".join(events))
        assert any(e == "token" and p == {"content": "直答"} for e, p in parsed)
        graph_payloads = [p for e, p in parsed if e == "graph"]
        assert graph_payloads and graph_payloads[0]["nodes"][0]["name"] == "VLAN"
        done_payloads = [p for e, p in parsed if e == "done"]
        assert done_payloads and done_payloads[0].get("retrieval_fallback") is True

    @pytest.mark.asyncio
    async def test_stream_no_fallback_when_tool_used(self, agent_with_mocks):
        agent = agent_with_mocks

        async def fake_events(*args, **kwargs):
            yield {"event": "on_tool_start", "name": "knowledge_search", "data": {}}
            yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="回答")}}

        fake_agent = MagicMock()
        fake_agent.astream_events = fake_events

        with patch("src.graphrag_agent.create_react_agent", return_value=fake_agent), \
             patch("src.graphrag_agent.AsyncSqliteSaver") as saver_cls, \
             patch("aiosqlite.connect", return_value=AsyncMock()):
            saver_cls.return_value.setup = AsyncMock()
            events = [e async for e in agent.query_stream("TCP", session_id="s1")]

        parsed = _parse_sse("".join(events))
        assert any(e == "token" for e, p in parsed)
        assert all(e != "graph" for e, p in parsed)
        done_payloads = [p for e, p in parsed if e == "done"]
        assert done_payloads and "retrieval_fallback" not in done_payloads[0]
        agent._entity_retriever.search_with_graph.assert_not_called()


# ---------- get_session_history ----------

class TestGetSessionHistory:

    def test_returns_history(self, agent_with_mocks):
        agent = agent_with_mocks
        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Q1"),
                AIMessage(content="A1"),
                HumanMessage(content="Q2"),
            ]
        }
        agent.agent.get_state.return_value = mock_state

        history = agent.get_session_history("s1")
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_returns_empty_on_error(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.agent.get_state.side_effect = Exception("fail")

        history = agent.get_session_history("s1")
        assert history == []


# ---------- close ----------

class TestClose:

    def test_closes_driver(self, agent_with_mocks):
        agent = agent_with_mocks
        agent.close()
        agent.driver.close.assert_called_once()


# ---------- make_tools 工厂 ----------

class TestMakeTools:

    def test_creates_four_tools(self, mock_entity_retriever, mock_stats_service):
        tools = _get_tools(mock_entity_retriever, mock_stats_service)
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {"knowledge_search", "graph_statistics", "node_search", "node_neighbors"}

    def test_tools_are_independent(self, mock_entity_retriever, mock_stats_service):
        """每个 make_tools 调用创建独立的工具实例"""
        from src.graphrag_agent import make_tools
        tools1 = make_tools(mock_entity_retriever, mock_stats_service)
        tools2 = make_tools(mock_entity_retriever, mock_stats_service)
        assert tools1[0] is not tools2[0]
