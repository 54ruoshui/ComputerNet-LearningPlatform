"""从 Neo4j 导出分层抽样的选择题评测集"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from neo4j import GraphDatabase  # noqa: E402


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip("\r")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    load_env()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    assert password, "NEO4J_PASSWORD 未设置"

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (q:MCQuestion)
            WHERE q.text IS NOT NULL AND q.correct_answer IS NOT NULL
              AND q.option_a IS NOT NULL AND q.option_d IS NOT NULL
            RETURN q.id AS id, q.text AS text,
                   q.option_a AS a, q.option_b AS b,
                   q.option_c AS c, q.option_d AS d,
                   q.correct_answer AS answer,
                   q.layer AS layer, q.difficulty AS difficulty
            """
        ).data()
    driver.close()

    by_layer = {}
    for r in rows:
        by_layer.setdefault(r["layer"] or "未知", []).append(r)

    total = len(rows)
    target = 100
    picked = []
    for layer, items in sorted(by_layer.items()):
        n = max(1, round(target * len(items) / total))
        step = max(1, len(items) // n)
        picked.extend(items[::step][:n])

    out = os.path.join(os.path.dirname(__file__), "eval_mcq.json")

    # 打乱选项顺序，避免原始答案分布偏斜（B 占 64% 会被模型钻空子）
    import random

    rng = random.Random(42)
    for q in picked:
        letters = ["A", "B", "C", "D"]
        opts = [q["a"], q["b"], q["c"], q["d"]]
        correct = opts[letters.index(q["answer"])]
        rng.shuffle(opts)
        q["a"], q["b"], q["c"], q["d"] = opts
        q["answer"] = letters[opts.index(correct)]

    with open(out, "w", encoding="utf-8") as f:
        json.dump(picked, f, ensure_ascii=False, indent=1)

    print(f"题库总数: {total}")
    for layer, items in sorted(by_layer.items()):
        print(f"  {layer}: {len(items)} 题, 抽了 {sum(1 for p in picked if p['layer']==layer)}")
    print(f"导出 {len(picked)} 题到 {out}")
    from collections import Counter
    print("答案分布:", dict(Counter(p["answer"] for p in picked)))


if __name__ == "__main__":
    main()
