"""build_graph_data 节点/关系一致性：悬空关系会让前端 d3 forceLink 抛错、布局中断"""

import contextlib

from src.services.graph_visualization_service import GraphVisualizationService


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeSession:
    def __init__(self, neighbor_names, rel_records, node_record):
        self._neighbor_names = neighbor_names
        self._rels = rel_records
        self._node = node_record

    def run(self, query, params=None):
        if ")-[r]-(neighbor" in query:
            # 每个 core 命中 3 个邻居（真实查询 LIMIT 3），全集覆盖全部邻居
            idx = int(str(params["src"])[4:])
            names = [self._neighbor_names[(idx + k) % len(self._neighbor_names)] for k in range(3)]
            return FakeResult([{"name": n, "labels": ["Entity"], "description": ""} for n in names])
        if ")-[r]->(n2)" in query:
            return FakeResult(self._rels)
        return FakeResult([self._node])


class FakeDriver:
    def __init__(self, session):
        self._session = session

    @contextlib.contextmanager
    def session(self):
        yield self._session


def _service(neighbor_names, rel_records):
    node_record = {"type": "Entity", "description": "", "name_en": ""}
    return GraphVisualizationService(FakeDriver(FakeSession(neighbor_names, rel_records, node_record)), lambda q: [])


def _entities(names):
    return [{"name": n, "score": 2.0, "entity_type": "Concept", "description": ""} for n in names]


def test_truncated_nodes_drop_dangling_relationships():
    """节点截断到 20 后，引用被截断节点的关系必须同步过滤"""
    cores = [f"core{i}" for i in range(10)]
    neighbors = [f"nb{i}" for i in range(12)]  # 10+12=22 > 20，必有 2 个被截断

    rels = []
    for i, nb in enumerate(neighbors):
        rels.append({"start": cores[i % 10], "rel_type": "ABOUT", "end": nb})
    for i in range(5):  # 邻居之间的边
        rels.append({"start": neighbors[i], "rel_type": "WORKS_WITH", "end": neighbors[(i + 1) % 12]})

    data = _service(neighbors, rels).build_graph_data(_entities(cores))

    assert len(data["nodes"]) == 20
    kept = {n["name"] for n in data["nodes"]}
    assert all(r["start"]["name"] in kept for r in data["relationships"])
    assert all(r["end"]["name"] in kept for r in data["relationships"])
    # 被截断的 2 个邻居的边确实被丢弃了，而不是残留悬空引用
    dropped = set(neighbors) - kept
    assert len(dropped) == 2
    assert len(data["relationships"]) == sum(
        1 for r in rels if r["start"] in kept and r["end"] in kept
    )


def test_relationship_referencing_unknown_node_dropped():
    """关系引用从未出现在节点集的名字（数据异常）时直接丢弃"""
    rels = [
        {"start": "core0", "rel_type": "ABOUT", "end": "nb0"},
        {"start": "core0", "rel_type": "ABOUT", "end": "ghost"},  # 不在任何节点集中
    ]

    data = _service(["nb0"], rels).build_graph_data(_entities(["core0"]))

    names = {n["name"] for n in data["nodes"]}
    assert "ghost" not in names
    assert len(data["relationships"]) == 1
    assert data["relationships"][0]["end"]["name"] == "nb0"


def test_empty_entities_returns_empty_graph():
    data = _service([], []).build_graph_data([])
    assert data == {"nodes": [], "relationships": []}
