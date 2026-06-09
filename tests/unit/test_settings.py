"""Settings 单元测试 — 必填字段校验、验证规则

注意：Settings 会读取 .env 文件，所以测试用 patch.dict(os.environ, ..., clear=True)
并临时隐藏 .env 文件来确保隔离。
"""

import os
import shutil
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """每个测试：隐藏 .env，确保只有显式设置的环境变量生效"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_file = os.path.join(project_root, ".env")
    backup = os.path.join(str(tmp_path), ".env.bak")

    if os.path.exists(env_file):
        shutil.move(env_file, backup)

    monkeypatch.chdir(project_root)
    yield

    if os.path.exists(backup):
        shutil.move(backup, env_file)


# ---------- 必填字段 ----------

class TestRequiredFields:

    def test_missing_neo4j_password_raises(self):
        with patch.dict(os.environ, {"ZHIPUAI_API_KEY": "key"}, clear=True):
            with pytest.raises(ValidationError, match="neo4j_password"):
                Settings()

    def test_missing_zhipuai_api_key_raises(self):
        with patch.dict(os.environ, {"NEO4J_PASSWORD": "pw"}, clear=True):
            with pytest.raises(ValidationError, match="zhipuai_api_key"):
                Settings()

    def test_all_required_fields_set(self):
        env = {
            "NEO4J_PASSWORD": "pw",
            "ZHIPUAI_API_KEY": "key",
        }
        with patch.dict(os.environ, env, clear=True):
            s = Settings()
            assert s.neo4j_password == "pw"
            assert s.zhipuai_api_key == "key"


# ---------- 默认值 ----------

class TestDefaults:

    def _make_settings(self, **extra_env):
        env = {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key", **extra_env}
        with patch.dict(os.environ, env, clear=True):
            return Settings()

    def test_neo4j_defaults(self):
        s = self._make_settings()
        assert s.neo4j_uri == "bolt://localhost:7687"
        assert s.neo4j_user == "neo4j"
        assert s.neo4j_database == "neo4j"

    def test_retrieval_defaults(self):
        s = self._make_settings()
        assert s.max_entities == 20
        assert s.max_context_tokens == 8000
        assert s.similarity_threshold == 0.7

    def test_llm_defaults(self):
        s = self._make_settings()
        assert s.temperature == 0.3
        assert s.max_tokens == 4096

    def test_web_defaults(self):
        s = self._make_settings()
        assert s.web_host == "0.0.0.0"
        assert s.web_port == 5001
        assert s.debug is False

    def test_optional_fields_default_empty(self):
        s = self._make_settings()
        assert s.api_key == ""
        assert s.qwen_api_key == ""
        assert s.dashscope_api_key == ""


# ---------- 字段约束 ----------

class TestFieldConstraints:

    def _make_settings(self, **extra_env):
        env = {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key", **extra_env}
        with patch.dict(os.environ, env, clear=True):
            return Settings()

    def test_max_entities_too_large(self):
        with pytest.raises(ValidationError):
            self._make_settings(MAX_ENTITIES="101")

    def test_max_entities_too_small(self):
        with pytest.raises(ValidationError):
            self._make_settings(MAX_ENTITIES="0")

    def test_similarity_threshold_valid(self):
        s = self._make_settings(SIMILARITY_THRESHOLD="0.85")
        assert s.similarity_threshold == 0.85

    def test_similarity_threshold_out_of_range(self):
        with pytest.raises(ValidationError):
            self._make_settings(SIMILARITY_THRESHOLD="1.5")

    def test_temperature_valid(self):
        s = self._make_settings(TEMPERATURE="1.0")
        assert s.temperature == 1.0

    def test_temperature_out_of_range(self):
        with pytest.raises(ValidationError):
            self._make_settings(TEMPERATURE="3.0")

    def test_web_port_valid(self):
        s = self._make_settings(WEB_PORT="8080")
        assert s.web_port == 8080

    def test_web_port_out_of_range(self):
        with pytest.raises(ValidationError):
            self._make_settings(WEB_PORT="99999")


# ---------- log_level 校验 ----------

class TestLogLevelValidation:

    def _make_settings(self, **extra_env):
        env = {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key", **extra_env}
        with patch.dict(os.environ, env, clear=True):
            return Settings()

    def test_valid_log_levels(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            s = self._make_settings(LOG_LEVEL=level)
            assert s.log_level == level

    def test_lowercase_normalized(self):
        s = self._make_settings(LOG_LEVEL="info")
        assert s.log_level == "INFO"

    def test_invalid_log_level(self):
        with pytest.raises(ValidationError, match="log_level"):
            self._make_settings(LOG_LEVEL="VERBOSE")


# ---------- log_format 校验 ----------

class TestLogFormatValidation:

    def _make_settings(self, **extra_env):
        env = {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key", **extra_env}
        with patch.dict(os.environ, env, clear=True):
            return Settings()

    def test_valid_formats(self):
        for fmt in ["console", "json"]:
            s = self._make_settings(LOG_FORMAT=fmt)
            assert s.log_format == fmt

    def test_invalid_format(self):
        with pytest.raises(ValidationError, match="log_format"):
            self._make_settings(LOG_FORMAT="xml")


# ---------- get_settings 缓存 ----------

class TestGetSettings:

    def test_returns_same_instance(self):
        with patch.dict(os.environ, {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key"}, clear=True):
            get_settings.cache_clear()
            s1 = get_settings()
            s2 = get_settings()
            assert s1 is s2

    def test_cache_cleared_returns_new(self):
        with patch.dict(os.environ, {"NEO4J_PASSWORD": "pw", "ZHIPUAI_API_KEY": "key"}, clear=True):
            s1 = get_settings()
            get_settings.cache_clear()
            s2 = get_settings()
            assert s1 is not s2


# ---------- extra 字段忽略 ----------

class TestExtraFields:

    def test_unknown_env_vars_ignored(self):
        env = {
            "NEO4J_PASSWORD": "pw",
            "ZHIPUAI_API_KEY": "key",
            "UNKNOWN_VAR": "whatever",
        }
        with patch.dict(os.environ, env, clear=True):
            s = Settings()
            assert not hasattr(s, "unknown_var")
