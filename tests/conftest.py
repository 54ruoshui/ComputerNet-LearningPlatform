"""Pytest 全局 fixtures 和共享配置"""

import os
import sys

import pytest

# 确保项目根目录在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 测试环境变量（必须在 import src 之前设置）
os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("ZHIPUAI_API_KEY", "test-zhipuai-key")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-dashscope-key")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """每个测试前重置 Settings 单例缓存，避免环境变量交叉污染"""
    from src.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_concurrency_gate():
    """每个测试前后重置 LLM 并发闸门（防测试用小容量闸门泄漏到其他用例）"""
    try:
        from web.concurrency import reset_gate
        reset_gate()
    except ImportError:
        pass
    yield
    try:
        from web.concurrency import reset_gate
        reset_gate()
    except ImportError:
        pass
