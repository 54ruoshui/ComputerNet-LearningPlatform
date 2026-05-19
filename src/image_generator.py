"""
知识图谱可视化图片生成器
使用 Graphviz 从结构化图谱数据直接渲染高清 PNG 图片
"""

import os
import re
import hashlib
import logging
import platform
import time
from collections import OrderedDict
from typing import Optional

import graphviz

# 如果 Graphviz 不在系统 PATH 中，手动指定安装路径
if platform.system() == "Windows":
    _custom_bin = r"D:\Graphviz\bin"
    if os.path.isdir(_custom_bin) and _custom_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _custom_bin + ";" + os.environ.get("PATH", "")

# 本地图片保存目录
IMAGE_SAVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "generated_images",
)

logger = logging.getLogger(__name__)

# 关系类型中文映射表
REL_TYPE_MAP = {
    "RELATES_TO": "",
    "HAS_ENTITY": "包含",
    "HAS_SUBCOMMUNITY": "子社区",
    "APPLY_TO": "应用于",
    "DEPENDS_ON": "依赖于",
    "RELATED_TO": "关联",
    "WORKS_WITH": "协同",
    "PROTECTS": "保护",
    "ATTACKS": "攻击",
    "SOLVED_BY": "解决方式",
    "BELONGS_TO": "属于",
    "HAS_FUNCTION": "功能",
    "BETWEEN": "介于",
    "OPERATES_AT": "工作在",
    "EVOLVED_FROM": "演进自",
    "IMPROVES_UPON": "改进于",
    "CONTRASTS_WITH": "对比",
    "IS_A_TYPE_OF": "是一种",
    "CONNECTS": "连接",
    "CONNECTS_TO": "连接到",
    "COMPLEMENTS": "互补",
    "COMPONENT_OF": "组成部分",
    "SUPPORTS": "支持",
    "IMPLEMENTS": "实现",
    "SOLVES": "解决",
    "REPLACES": "替代",
    "EXTENDS": "扩展",
    "ENABLES": "使能",
    "USES": "使用",
    "PART_OF": "一部分",
    "INCLUDES": "包含",
    "DEFENDS_AGAINST": "防御",
    "EXPLOITS": "利用",
    "DESCRIBES": "描述",
    "ABOVE": "位于...上",
    "CONTAINS": "包含",
    "DERIVED_FROM": "派生自",
    "AFFECTS": "影响",
    "CONSTRAINED_BY": "受限于",
    "ALTERNATIVE_TO": "替代方案",
    "INTERACTS_WITH": "交互",
    "BRIDGE_TO": "桥接",
}

# 节点配色（与前端 D3.js neo4j-graph.js 一致）
NODE_COLORS = {
    "Protocol": "#4A90E2",
    "Device": "#7B68EE",
    "Layer": "#48D1CC",
    "Concept": "#3CB371",
    "Entity": "#6B8E23",
    "Question": "#FFB347",
    "Answer": "#FF69B4",
    "Problem": "#FF6B6B",
    "Solution": "#4ECDC4",
    "default": "#95A5A6",
}


def _detect_chinese_font() -> str:
    """检测当前系统中可用的中文字体"""
    system = platform.system()
    if system == "Windows":
        return "Microsoft YaHei"
    elif system == "Darwin":
        return "PingFang SC"
    else:
        return "WenQuanYi Micro Hei"


class _LRUCache:
    """简单的 LRU 缓存"""

    def __init__(self, maxsize: int = 64, ttl: int = 7200):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str) -> Optional[str]:
        if key in self._cache:
            value, ts = self._cache[key]
            if time.time() - ts < self._ttl:
                self._cache.move_to_end(key)
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: str):
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self._maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = (value, time.time())


class KnowledgeGraphImageGenerator:
    """使用 Graphviz 渲染知识图谱可视化图片"""

    def __init__(self):
        self._font = _detect_chinese_font()
        self._cache = _LRUCache(maxsize=64, ttl=7200)
        self._available = self._check_graphviz()

    def is_available(self) -> bool:
        return self._available

    def generate(self, graph_data: dict, question: str) -> dict:
        """
        使用 Graphviz 生成知识图谱可视化图片。

        Returns:
            {"image_url": "/generated_images/xxx.png"} 或 {"error": "..."}
        """
        nodes = graph_data.get("nodes", [])
        relationships = graph_data.get("relationships", [])

        if not nodes and not relationships:
            return {"error": "图谱数据为空，无法生成图片"}

        cache_key = self._cache_key(graph_data, question)
        cached = self._cache.get(cache_key)
        if cached:
            logger.info("命中图片缓存")
            return {"image_url": cached}

        try:
            image_url = self._render_graphviz(nodes, relationships, question, cache_key)
            if image_url:
                self._cache.set(cache_key, image_url)
                return {"image_url": image_url}
            return {"error": "Graphviz 渲染失败"}
        except Exception as e:
            logger.error(f"Graphviz 渲染异常: {e}")
            return {"error": f"图谱渲染失败: {e}"}

    # ------------------------------------------------------------------
    # private
    # ------------------------------------------------------------------

    def _check_graphviz(self) -> bool:
        """检查 Graphviz Python 包和系统软件是否可用"""
        try:
            g = graphviz.Digraph(format="png")
            g.attr(fontname=self._font)
            g.node("test", label="测试", fontname=self._font)
            output = g.pipe(format="png")
            return output is not None and len(output) > 0
        except Exception as e:
            logger.warning(f"Graphviz 不可用: {e}")
            return False

    def _render_graphviz(
        self,
        nodes: list,
        relationships: list,
        question: str,
        cache_key: str,
    ) -> Optional[str]:
        """构建 Graphviz Digraph 并渲染为 PNG"""
        dot = graphviz.Digraph(
            format="png",
            graph_attr={
                "bgcolor": "#FAFAFA",
                "rankdir": "TB",
                "splines": "spline",
                "nodesep": "0.8",
                "ranksep": "1.0",
                "pad": "0.5",
                "dpi": "200",
                "size": "10,8!",
                "ratio": "fill",
                "fontname": self._font,
                "label": question[:50],
                "labelloc": "t",
                "fontsize": "20",
                "fontcolor": "#2C3E50",
                "overlap": "scale",
                "outputorder": "edgesfirst",
                "esep": "+5",
            },
            node_attr={
                "fontname": self._font,
                "fontsize": "13",
                "style": "filled,rounded",
                "shape": "box",
                "penwidth": "2",
                "margin": "0.2,0.1",
            },
            edge_attr={
                "fontname": self._font,
                "fontsize": "10",
                "color": "#BBBBBB",
                "fontcolor": "#888888",
                "penwidth": "1.5",
                "arrowsize": "0.7",
                "arrowhead": "vee",
                "labeldistance": "2.0",
                "labelfloat": "true",
            },
        )

        # 添加节点
        node_ids = set()
        for node in nodes:
            name = node.get("name", "Unknown")
            node_type = node.get("type", "default")
            node_id = self._safe_node_id(name)

            if node_id not in node_ids:
                color = NODE_COLORS.get(node_type, NODE_COLORS["default"])
                fill_color = self._lighten(color, 0.85)
                dot.node(
                    node_id,
                    label=self._escape_label(name),
                    fillcolor=fill_color,
                    color=color,
                    fontcolor="#333333",
                )
                node_ids.add(node_id)

        # 添加边
        added_edges = set()
        for rel in relationships:
            start = rel.get("start", {})
            end = rel.get("end", {})
            rel_type = rel.get("type", "")

            start_name = start.get("name", "") if isinstance(start, dict) else str(start)
            end_name = end.get("name", "") if isinstance(end, dict) else str(end)

            start_id = self._safe_node_id(start_name)
            end_id = self._safe_node_id(end_name)

            # 翻译关系类型为中文
            translated = REL_TYPE_MAP.get(rel_type, rel_type)
            edge_key = (start_id, end_id, rel_type)
            if edge_key not in added_edges and start_id != end_id:
                if translated:  # 空字符串表示不显示标签
                    dot.edge(start_id, end_id, label=self._escape_label(translated))
                else:
                    dot.edge(start_id, end_id)
                added_edges.add(edge_key)

        # 渲染并保存
        os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
        filepath = os.path.join(IMAGE_SAVE_DIR, cache_key)
        dot.render(filename=filepath, cleanup=True)

        local_url = f"/generated_images/{cache_key}.png"
        logger.info(f"Graphviz 图片已生成: {filepath}.png")
        return local_url

    @staticmethod
    def _cache_key(graph_data: dict, question: str) -> str:
        raw = question + str(graph_data.get("nodes", []))[:500]
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _safe_node_id(name: str) -> str:
        """生成合法的 Graphviz 节点 ID"""
        safe = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]", "_", name)
        return f"n_{safe}"

    @staticmethod
    def _escape_label(text: str) -> str:
        """转义 Graphviz 标签中的特殊字符"""
        return text.replace('"', '\\"').replace("\\", "\\\\")

    @staticmethod
    def _lighten(color: str, factor: float = 0.85) -> str:
        """将颜色变浅（用于节点填充色）"""
        hex_color = color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)
        return f"#{r:02x}{g:02x}{b:02x}"
