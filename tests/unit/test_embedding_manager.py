"""EmbeddingManager 单元测试 — mock DashScope API"""

from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import EmbeddingError


# ---------- Fixtures ----------

@pytest.fixture
def embedding_mgr():
    """创建 EmbeddingManager 实例（mock 内部依赖）"""
    with patch("src.embedding_manager.get_settings") as mock_settings:
        s = MagicMock()
        s.qwen_api_key = "test-key"
        s.dashscope_api_key = ""
        s.qwen_embedding_model = "text-embedding-v3"
        mock_settings.return_value = s

        from src.embedding_manager import EmbeddingManager
        mgr = EmbeddingManager.__new__(EmbeddingManager)
        mgr.model_name = "text-embedding-v3"
        mgr.dimension = 1024
        mgr._ready = True
        mgr._text_embedding = MagicMock()
        return mgr


def _success_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.output = {"embeddings": [{"embedding": [0.1] * 1024}]}
    return mock_resp


# ---------- 测试 embed_query ----------

class TestEmbedQuery:

    def test_returns_embedding_vector(self, embedding_mgr):
        embedding_mgr._text_embedding.call.return_value = _success_response()

        result = embedding_mgr.embed_query("TCP协议")

        assert result is not None
        assert len(result) == 1024
        embedding_mgr._text_embedding.call.assert_called_once_with(
            model="text-embedding-v3", input="TCP协议"
        )

    def test_returns_none_when_not_ready(self, embedding_mgr):
        embedding_mgr._ready = False
        result = embedding_mgr.embed_query("test")
        assert result is None

    def test_returns_none_for_empty_string(self, embedding_mgr):
        result = embedding_mgr.embed_query("  ")
        assert result is None

    def test_returns_none_on_api_error(self, embedding_mgr):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        embedding_mgr._text_embedding.call.return_value = mock_resp

        result = embedding_mgr.embed_query("test")
        assert result is None

    def test_returns_none_on_exception(self, embedding_mgr):
        embedding_mgr._text_embedding.call.side_effect = RuntimeError("boom")

        result = embedding_mgr.embed_query("test")
        assert result is None

    def test_strips_whitespace(self, embedding_mgr):
        embedding_mgr._text_embedding.call.return_value = _success_response()

        embedding_mgr.embed_query("  hello  ")
        embedding_mgr._text_embedding.call.assert_called_once_with(
            model="text-embedding-v3", input="hello"
        )


# ---------- 测试 embed_texts ----------

class TestEmbedTexts:

    def test_batch_embedding(self, embedding_mgr):
        embedding_mgr._text_embedding.call.return_value = _success_response()

        results = embedding_mgr.embed_texts(["TCP", "UDP"])

        assert len(results) == 2
        assert all(r is not None for r in results)

    def test_batch_with_empty_string(self, embedding_mgr):
        embedding_mgr._text_embedding.call.return_value = _success_response()
        results = embedding_mgr.embed_texts(["", "TCP"])
        assert results[0] is None
        assert results[1] is not None

    def test_empty_list(self, embedding_mgr):
        results = embedding_mgr.embed_texts([])
        assert results == []


# ---------- 测试 _call_embedding_api 重试 ----------

class TestCallEmbeddingApi:

    def test_retries_on_embedding_error(self, embedding_mgr):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        embedding_mgr._text_embedding.call.return_value = mock_resp

        with pytest.raises(EmbeddingError):
            embedding_mgr._call_embedding_api("test")

        assert embedding_mgr._text_embedding.call.call_count == 3

    def test_succeeds_after_retry(self, embedding_mgr):
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_success = _success_response()

        embedding_mgr._text_embedding.call.side_effect = [mock_fail, mock_success]

        result = embedding_mgr._call_embedding_api("test")
        assert result == [0.1] * 1024
        assert embedding_mgr._text_embedding.call.call_count == 2


# ---------- 测试 ready 属性 ----------

class TestReady:

    def test_ready_property(self, embedding_mgr):
        assert embedding_mgr.ready is True
        embedding_mgr._ready = False
        assert embedding_mgr.ready is False
