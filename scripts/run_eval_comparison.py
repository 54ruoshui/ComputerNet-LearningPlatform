#!/usr/bin/env python3
"""
系统 vs 通用大模型 对比实验评测脚本（支撑 docs/报告/对比实验报告.md）

双臂对比：
  baseline — 通用大模型直答（ZhipuAI，无 RAG）
  system   — 本系统（GraphRAG Agent，混合检索 + 知识图谱上下文）

指标：
  1. 客观题准确率（MCQ，按 OSI 层统计）
  2. 开放问答知识覆盖度（答案提及的图谱实体 ∩ 题目 ABOUT 关联实体）
  3. 开放问答 LLM-as-judge 盲评（win/tie/loss，随机交换答案位置消位置偏差）

用法：
    python scripts/run_eval_comparison.py                                  # 默认 50 MCQ + 20 QA
    python scripts/run_eval_comparison.py --mcq-count 1 --qa-count 2 --limit 10   # 冒烟
    python scripts/run_eval_comparison.py --report                         # 全部完成后重汇总并生成报告
    python scripts/run_eval_comparison.py --judge-model glm-4-plus         # 换强模型做评判

结果增量写入 eval/comparison_partial.jsonl（每行一个任务，可 Ctrl-C 中断后重跑续传），
汇总输出 eval/comparison_results.json，--report 生成 docs/报告/对比实验报告.md。
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("eval_comparison")

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from src.settings import get_settings

ALL_LAYERS = ["物理层", "数据链路层", "网络层", "传输层", "应用层"]

# 与 finetune/eval/eval_mcq_infer.py 保持一致的阅卷提示词
MCQ_BASELINE_SYSTEM = "你是一位计算机网络课程的阅卷老师。只输出正确选项的字母（A、B、C 或 D），不要输出任何其他内容。"
# 对齐 graphrag_agent._build_system_prompt 的角色与回答要求（去掉工具部分），保证两臂输出风格公平
OPEN_BASELINE_SYSTEM = (
    "你是一个计算机网络领域的资深专家和优秀教师。\n"
    "使用中文回答，使用 Markdown 格式。回答详尽、结构清晰，解释原理并举例，"
    "涉及多个概念时进行对比，最后给出总结。"
)
MCQ_SUFFIX = "\n\n请直接给出正确选项的字母（A、B、C 或 D），不要展开解释。"

# GraphRAGAgent.query 内部捕获异常后会把错误文案作为 answer 返回（graphrag_agent.py）
SYSTEM_ERROR_MARKER = "查询处理失败"


def query_system(agent, question: str, retries: int = 3) -> dict:
    """调用系统臂并重试；Agent 网络错误以错误文案形式返回，须识别并重试"""
    last_err = None
    for attempt in range(retries):
        result = agent.query(question, session_id=f"eval_{uuid.uuid4().hex[:8]}",
                             mastery_context="")
        if SYSTEM_ERROR_MARKER not in (result.get("answer") or ""):
            return result
        last_err = result["answer"]
        logger.warning("系统臂返回错误文案（第 %d 次）: %s", attempt + 1, last_err[:120])
    raise RuntimeError(f"系统臂连续 {retries} 次失败: {last_err[:200]}")

JUDGE_SYSTEM = (
    "你是一位计算机网络课程的教学督导，负责盲评两份对学生提问的回答。\n"
    "从准确性、知识覆盖度、结构清晰度、教学适用性四个维度综合判断哪份回答更好。\n"
    '只输出 JSON：{"verdict": "A|B|tie", "reason": "一句话理由"}，不要输出其他内容。'
)


# ==================== 工具函数 ====================

def parse_letter(text: str) -> str:
    """从回答文本中抽取选项字母，分级匹配；失败返回 '?'

    系统臂回答常先回显题面选项再给结论（如"正确选项是：B"），因此：
    1. 显式模式要覆盖"选项"措辞；2. 末级兜底取最后一个独立字母——结论在末尾，
    取第一个会命中回显选项行里的"A."。"""
    if not text:
        return "?"
    m = re.search(r"正确(?:答案|选项)[是:：]?\s*([ABCD])", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:答案|选项)[是:：]?\s*([ABCD])(?![A-Za-z])", text)
    if m:
        return m.group(1)
    m = re.search(r"^([ABCD])[\s。.、]?$", text.strip(), re.MULTILINE)
    if m:
        return m.group(1)
    letters = re.findall(r"([ABCD])(?![A-Za-z])", text.upper())
    return letters[-1] if letters else "?"


def parse_json_content(content: str):
    """剥 markdown 围栏 + 容错解析 JSON"""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def coverage_score(answer: str, gt_entities: list, all_names: list):
    """知识覆盖度：答案提及实体 ∩ ground truth / |gt|；返回 (覆盖率, 命中列表)"""
    from src.services.entity_service import EntityService

    extracted = set(EntityService.extract_entities_from_text(answer or "", all_names))
    hits = [e for e in gt_entities if e in extracted]
    return (len(hits) / len(gt_entities) if gt_entities else 0.0), hits


# ==================== 基线臂（通用大模型直答） ====================

class BaselineRunner:
    def __init__(self, model: str = None):
        settings = get_settings()
        self._client = OpenAI(api_key=settings.zhipuai_api_key, base_url=settings.zhipuai_base_url)
        self._model = model or settings.zhipuai_model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def chat(self, system: str, user: str, max_tokens: int = 4096) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    def answer_mcq(self, item: dict) -> str:
        user = (
            f"{item['text']}\n"
            f"A. {item['option_a']}\nB. {item['option_b']}\n"
            f"C. {item['option_c']}\nD. {item['option_d']}"
        )
        return self.chat(MCQ_BASELINE_SYSTEM, user, max_tokens=32)

    def answer_open(self, question: str) -> str:
        return self.chat(OPEN_BASELINE_SYSTEM, question)


# ==================== 评判 ====================

class JudgeRunner:
    def __init__(self, model: str):
        settings = get_settings()
        self._client = OpenAI(api_key=settings.zhipuai_api_key, base_url=settings.zhipuai_base_url)
        self._model = model

    def judge(self, question: str, ans_sys: str, ans_base: str, swap: bool) -> dict:
        """盲评；swap=True 时交换两答案位置并在返回前翻转 verdict（消位置偏差）"""
        first, second = (ans_base, ans_sys) if swap else (ans_sys, ans_base)
        user = (
            f"学生提问：{question}\n\n"
            f"【回答 A】\n{first}\n\n【回答 B】\n{second}\n\n"
            "请判断哪份回答更好，输出 JSON。"
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
            max_tokens=256,
            temperature=0.1,
        )
        data = parse_json_content(resp.choices[0].message.content or "")
        verdict_raw = (data or {}).get("verdict", "tie")
        reason = (data or {}).get("reason", "")
        # 统一转为相对系统的 win/tie/loss
        if verdict_raw == "A":
            verdict = "loss" if swap else "win"
        elif verdict_raw == "B":
            verdict = "win" if swap else "loss"
        else:
            verdict = "tie"
        return {"verdict": verdict, "reason": reason}


# ==================== 抽样（Python 侧种子抽样，保证重跑确定性） ====================

def sample_mcq(session, per_layer: int, layers: list, seed: int) -> list:
    rows = session.run(
        """
        MATCH (q:MCQuestion)
        WHERE q.correct_answer IN ['A', 'B', 'C', 'D']
        RETURN q.id AS id, q.text AS text, q.option_a AS option_a,
               q.option_b AS option_b, q.option_c AS option_c,
               q.option_d AS option_d, q.correct_answer AS answer, q.layer AS layer
        """
    ).data()
    rng = random.Random(f"{seed}:mcq")
    picked = []
    for layer in layers:
        pool = [r for r in rows if r.get("layer") == layer]
        picked.extend(rng.sample(pool, min(per_layer, len(pool))))
    return picked


def sample_open_questions(session, count: int, layers: list, seed: int) -> list:
    rows = session.run(
        """
        MATCH (q:Question)
        OPTIONAL MATCH (q)-[:ABOUT]->(e:Entity)
        RETURN q.id AS id, q.text AS question, q.layer AS layer,
               collect(DISTINCT e.name) AS gt_entities
        """
    ).data()
    pool = [
        r for r in rows
        if r.get("gt_entities") and (not layers or r.get("layer") in layers)
    ]
    rng = random.Random(f"{seed}:qa")
    return rng.sample(pool, min(count, len(pool)))


# ==================== 增量执行 ====================

def load_done(partial_path: Path) -> dict:
    results = {}
    if partial_path.exists():
        with open(partial_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    results[item["key"]] = item["result"]
                except json.JSONDecodeError:
                    continue
    return results


def append_partial(partial_path: Path, key: str, result: dict):
    with open(partial_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "result": result}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def run_task(key: str, fn, partial_path: Path, done: dict):
    """执行单个任务；异常时不写盘（下次重跑会重试），失败返回 None"""
    if key in done:
        return done[key]
    start = time.time()
    try:
        result = fn()
    except Exception as e:
        logger.error("任务失败（已跳过，重跑时将重试）: %s - %s", key, e)
        return None
    result["elapsed"] = round(time.time() - start, 2)
    append_partial(partial_path, key, result)
    done[key] = result
    return result


# ==================== 汇总与报告 ====================

def aggregate(mcq_items: list, qa_items: list, done: dict, meta: dict) -> dict:
    def arm_accuracy(arm: str):
        by_layer, total, correct, parse_fail = {}, 0, 0, 0
        for it in mcq_items:
            r = done.get(f"mcq:{it['id']}:{arm}")
            if not r:
                continue
            total += 1
            layer = it.get("layer") or "未知"
            by_layer.setdefault(layer, [0, 0])
            if r.get("letter") == "?":
                parse_fail += 1
            if r.get("letter") == it["answer"]:
                correct += 1
                by_layer[layer][0] += 1
            by_layer[layer][1] += 1
        return {
            "mcq_total": total,
            "mcq_accuracy": round(correct / total, 4) if total else 0.0,
            "mcq_by_layer": {k: round(v[0] / v[1], 4) for k, v in sorted(by_layer.items()) if v[1]},
            "mcq_parse_fail": parse_fail,
        }

    detail_mcq, detail_qa = [], []
    sys_cover_sum, base_cover_sum, sys_retrieval_sum = 0.0, 0.0, 0.0
    for it in mcq_items:
        b, s = done.get(f"mcq:{it['id']}:baseline"), done.get(f"mcq:{it['id']}:system")
        detail_mcq.append({
            "id": it["id"], "layer": it.get("layer"), "answer": it["answer"],
            "baseline": (b or {}).get("letter"), "system": (s or {}).get("letter"),
        })
    for it in qa_items:
        b = done.get(f"qa:{it['id']}:baseline")
        s = done.get(f"qa:{it['id']}:system")
        j = done.get(f"qa:{it['id']}:judge")
        if b and s:
            base_cover_sum += b.get("coverage", 0.0)
            sys_cover_sum += s.get("coverage", 0.0)
            sys_retrieval_sum += s.get("retrieval_hit", 0.0)
        detail_qa.append({
            "id": it["id"], "layer": it.get("layer"), "question": it["question"],
            "gt_entities": it["gt_entities"],
            "baseline_coverage": (b or {}).get("coverage"),
            "system_coverage": (s or {}).get("coverage"),
            "system_retrieval_hit": (s or {}).get("retrieval_hit"),
            "judge": (j or {}).get("verdict"),
            "judge_reason": (j or {}).get("reason"),
        })

    qa_done = sum(1 for it in qa_items if f"qa:{it['id']}:baseline" in done)
    judge_counts = {"win": 0, "tie": 0, "loss": 0}
    for it in qa_items:
        j = done.get(f"qa:{it['id']}:judge")
        if j and j.get("verdict") in judge_counts:
            judge_counts[j["verdict"]] += 1
    judged = sum(judge_counts.values())

    baseline = arm_accuracy("baseline")
    baseline["qa_coverage"] = round(base_cover_sum / qa_done, 4) if qa_done else 0.0
    system = arm_accuracy("system")
    system.update({
        "qa_coverage": round(sys_cover_sum / qa_done, 4) if qa_done else 0.0,
        "qa_retrieval_hit": round(sys_retrieval_sum / qa_done, 4) if qa_done else 0.0,
    })
    return {
        "baseline": baseline,
        "system": system,
        "judge": {
            **judge_counts,
            "win_rate": round(judge_counts["win"] / judged, 4) if judged else 0.0,
            "judged": judged,
        },
        "detail_mcq": detail_mcq,
        "detail_qa": detail_qa,
        "meta": meta,
    }


def load_feedback_stats() -> dict:
    db_path = project_root / "data" / "memory" / "mastery.db"
    if not db_path.exists():
        return {"total": 0, "positive": 0, "negative": 0, "satisfaction_rate": 0.0, "by_day": []}
    try:
        from src.mastery_tracker import MasteryTracker

        tracker = MasteryTracker(db_path=str(db_path))
        try:
            return tracker.get_feedback_stats()
        finally:
            tracker.close()
    except Exception as e:
        logger.warning("读取满意度统计失败: %s", e)
        return {"total": 0, "positive": 0, "negative": 0, "satisfaction_rate": 0.0, "by_day": []}


def pct(x) -> str:
    return f"{(x or 0) * 100:.1f}%"


def generate_report(results: dict, feedback: dict, out_path: Path):
    meta, base, sysr, judge = results["meta"], results["baseline"], results["system"], results["judge"]
    lines = []
    add = lines.append

    add("# GraphRAG 系统与通用大模型对比实验报告")
    add("")
    add(f"> 生成时间：{meta['timestamp']} ｜ 评测脚本：`scripts/run_eval_comparison.py` ｜ 随机种子：{meta['seed']}")
    add("")

    add("## 一、实验目标")
    add("")
    add("依据《基于AIGC的动态课程内容生成》项目申报书要求，将本系统（GraphRAG 混合检索 + 知识图谱上下文 + ReAct Agent）"
        "与通用大模型直接生成方案对比，从**回答准确性、知识覆盖度、学生满意度**三个维度进行量化评估。")
    add("")

    add("## 二、评测设计")
    add("")
    add("| 维度 | 指标 | 说明 |")
    add("|---|---|---|")
    add(f"| 准确性 | 客观题准确率 | {meta['mcq_total']} 道选择题（MCQuestion，按 OSI 层分层抽样），正则解析回答中的选项字母 |")
    add(f"| 知识覆盖度 | 实体覆盖率 | 开放问答答案提及的图谱实体 ∩ 题目 ABOUT 关联实体 ÷ 关联实体数（{meta['qa_count']} 题） |")
    add("| 知识覆盖度 | 检索命中率（仅系统） | 检索返回实体 ∩ ABOUT 关联实体 ÷ 关联实体数 |")
    add("| 回答质量 | LLM 盲评 | 评判模型 win/tie/loss 三分类，随机交换答案位置消除位置偏差 |")
    add("| 学生满意度 | 用户反馈 | 答题界面 👍/👎 采集（`answer_feedback` 表） |")
    add("")

    add("## 三、实验环境")
    add("")
    add("| 项目 | 配置 |")
    add("|---|---|")
    add(f"| 基线（直答）模型 | {meta['baseline_model']}（无 RAG） |")
    add(f"| 系统模型 | GraphRAG Agent（{meta['baseline_model']} + 知识图谱混合检索） |")
    add(f"| 评判模型 | {meta['judge_model']} |")
    add(f"| 抽样层 | {'、'.join(meta['layers'])} |")
    add(f"| 题量 | MCQ {meta['mcq_total']} 题 ／ 开放问答 {meta['qa_count']} 题 |")
    add("")

    add("## 四、客观题准确率")
    add("")
    add("| OSI 层 | 通用大模型直答 | 本系统 | 差值 |")
    add("|---|---|---|---|")
    layers = sorted(set(base["mcq_by_layer"]) | set(sysr["mcq_by_layer"]))
    for layer in layers:
        b, s = base["mcq_by_layer"].get(layer, 0.0), sysr["mcq_by_layer"].get(layer, 0.0)
        add(f"| {layer} | {pct(b)} | {pct(s)} | {(s - b) * 100:+.1f}pp |")
    add(f"| **整体** | **{pct(base['mcq_accuracy'])}** | **{pct(sysr['mcq_accuracy'])}** | "
        f"**{(sysr['mcq_accuracy'] - base['mcq_accuracy']) * 100:+.1f}pp** |")
    add("")
    add(f"字母解析失败（记为错误）：基线 {base['mcq_parse_fail']} 题、系统 {sysr['mcq_parse_fail']} 题。")
    add("")

    add("## 五、开放问答：知识覆盖度")
    add("")
    add("| 指标 | 通用大模型直答 | 本系统 |")
    add("|---|---|---|")
    add(f"| 平均实体覆盖率 | {pct(base['qa_coverage'])} | {pct(sysr['qa_coverage'])} |")
    add(f"| 平均检索命中率 | — | {pct(sysr['qa_retrieval_hit'])} |")
    add("")
    add("覆盖度衡量的是答案与教材知识图谱（标准知识点集合）的对齐程度，而非信息量本身。")
    add("")

    add("## 六、LLM 盲评对比")
    add("")
    add("| 结果 | 数量 | 占比 |")
    add("|---|---|---|")
    for k, label in (("win", "系统更好"), ("tie", "持平"), ("loss", "基线更好")):
        n = judge[k]
        add(f"| {label} | {n} | {pct(n / judge['judged']) if judge['judged'] else '—'} |")
    add("")
    judged_items = [d for d in results["detail_qa"] if d.get("judge") and d["judge"] != "tie"]
    for d in judged_items[:3]:
        add(f"- **{d['question'][:40]}…**（{d['judge']}）：{d.get('judge_reason') or ''}")
    add("")

    add("## 七、用户满意度")
    add("")
    if feedback["total"]:
        add("| 总反馈 | 满意 👍 | 不满意 👎 | 满意率 |")
        add("|---|---|---|---|")
        add(f"| {feedback['total']} | {feedback['positive']} | {feedback['negative']} | {pct(feedback['satisfaction_rate'])} |")
    else:
        add("暂无用户反馈数据（试用阶段持续采集，报告可随时用 `--report` 重新生成）。")
    add("")

    add("## 八、结论与局限")
    add("")
    acc_delta = (sysr["mcq_accuracy"] - base["mcq_accuracy"]) * 100
    cov_delta = (sysr["qa_coverage"] - base["qa_coverage"]) * 100
    add(f"在本次抽样下，系统相对通用大模型直答：客观题准确率 {acc_delta:+.1f}pp，"
        f"知识覆盖度 {cov_delta:+.1f}pp，盲评胜率 {pct(judge['win_rate'])}。")
    add("")
    add("局限：")
    add("1. 评判模型与被评模型同属 ZhipuAI 系列，可能存在自偏好；")
    add("2. 样本量有限（MCQ 按层抽样、开放问答来自图谱 Question 节点），结论存在抽样波动；")
    add("3. 覆盖度指标依赖图谱实体命名匹配，同义表述（如中英文别名）可能被低估；")
    add("4. 满意度来自真实试用积累，样本量随试用推进增加。")
    add("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已生成: %s", out_path)


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description="GraphRAG 系统 vs 通用大模型 对比评测")
    parser.add_argument("--mcq-count", type=int, default=10, help="每层 MCQ 抽样数（默认 10，5 层共 50）")
    parser.add_argument("--qa-count", type=int, default=20, help="开放问答抽样数（默认 20）")
    parser.add_argument("--layers", default=",".join(ALL_LAYERS), help="逗号分隔的层过滤，默认全部")
    parser.add_argument("--limit", type=int, default=0, help="MCQ/QA 各自的题数硬上限（冒烟用）")
    parser.add_argument("--judge-model", default=None, help="评判模型，默认与系统主模型相同")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=str(project_root / "eval" / "comparison_results.json"))
    parser.add_argument("--partial", default=str(project_root / "eval" / "comparison_partial.jsonl"))
    parser.add_argument("--report", nargs="?", const=str(project_root / "docs" / "对比实验报告.md"),
                        help="评测完成后生成对比实验报告（默认路径 docs/报告/对比实验报告.md）")
    args = parser.parse_args()

    layers = [l.strip() for l in args.layers.split(",") if l.strip() in ALL_LAYERS] or ALL_LAYERS
    settings = get_settings()
    judge_model = args.judge_model or settings.zhipuai_model

    from src.langchain_config import LangGraphRAGConfig
    from src.graphrag_agent import GraphRAGAgent
    from src.services.entity_service import EntityService

    logger.info("初始化 GraphRAGAgent（Neo4j + LLM）...")
    agent = GraphRAGAgent(LangGraphRAGConfig())
    entity_service = EntityService(agent.driver)
    all_names = entity_service.get_all_entity_names()
    baseline = BaselineRunner()

    partial_path = Path(args.partial)
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(partial_path)

    try:
        with agent.driver.session() as session:
            mcq_items = sample_mcq(session, args.mcq_count, layers, args.seed)
            qa_items = sample_open_questions(session, args.qa_count, layers, args.seed)
        if args.limit:
            mcq_items, qa_items = mcq_items[:args.limit], qa_items[:args.limit]
        logger.info("抽样完成：MCQ %d 题、开放问答 %d 题（已完成任务 %d 个，将跳过）",
                    len(mcq_items), len(qa_items), len(done))
        est = len(mcq_items) * 22 + len(qa_items) * 45 + len(qa_items) * 5
        logger.info("预估剩余时长约 %d 分钟", est // 60)

        # ---------- MCQ：两臂 ----------
        for i, it in enumerate(mcq_items, 1):
            q_text = (f"{it['text']}\nA. {it['option_a']}\nB. {it['option_b']}\n"
                      f"C. {it['option_c']}\nD. {it['option_d']}")

            def run_baseline(_it=it, _q=q_text):
                return {"letter": parse_letter(baseline.answer_mcq(_it))}

            r = run_task(f"mcq:{it['id']}:baseline", run_baseline, partial_path, done)
            if r:
                logger.info("[mcq %d/%d] baseline %s %ss", i, len(mcq_items), it['id'], r.get("elapsed"))

            def run_system(_q=q_text):
                result = query_system(agent, _q + MCQ_SUFFIX)
                return {"letter": parse_letter(result["answer"]), "raw_answer": result["answer"][:500]}

            r = run_task(f"mcq:{it['id']}:system", run_system, partial_path, done)
            if r:
                logger.info("[mcq %d/%d] system %s %ss -> %s", i, len(mcq_items), it['id'],
                            r.get("elapsed"), r.get("letter"))

        # ---------- 开放问答：两臂 + 覆盖度 ----------
        for i, it in enumerate(qa_items, 1):
            qid, question, gt = it["id"], it["question"], it["gt_entities"]

            def run_qa_baseline(_q=question, _gt=gt):
                ans = baseline.answer_open(_q)
                cov, hits = coverage_score(ans, _gt, all_names)
                return {"answer": ans, "coverage": round(cov, 4), "hits": hits}

            r = run_task(f"qa:{qid}:baseline", run_qa_baseline, partial_path, done)
            if r:
                logger.info("[qa %d/%d] baseline %s %ss cov=%.2f", i, len(qa_items), qid,
                            r.get("elapsed"), r.get("coverage") or 0)

            def run_qa_system(_q=question, _gt=gt):
                result = query_system(agent, _q)
                ans = result["answer"]
                retrieved = [n.get("name") for n in (result.get("graph_data") or {}).get("nodes", [])]
                cov, hits = coverage_score(ans, _gt, all_names)
                ret = [e for e in _gt if e in set(retrieved)]
                return {
                    "answer": ans, "coverage": round(cov, 4),
                    "retrieval_hit": round(len(ret) / len(_gt), 4) if _gt else 0.0,
                    "retrieved": retrieved[:20],
                }

            r = run_task(f"qa:{qid}:system", run_qa_system, partial_path, done)
            if r:
                logger.info("[qa %d/%d] system %s %ss cov=%.2f", i, len(qa_items), qid,
                            r.get("elapsed"), r.get("coverage") or 0)

        # ---------- 盲评 ----------
        judge = JudgeRunner(judge_model)
        for i, it in enumerate(qa_items, 1):
            qid = it["id"]

            def run_judge(_it=it, _qid=qid):
                b, s = done.get(f"qa:{_qid}:baseline"), done.get(f"qa:{_qid}:system")
                if not b or not s:
                    raise RuntimeError(f"qa:{_qid} 缺少答案，无法盲评")
                if SYSTEM_ERROR_MARKER in b["answer"] or SYSTEM_ERROR_MARKER in s["answer"]:
                    raise RuntimeError(f"qa:{_qid} 存在错误文案答案，拒绝盲评")
                swap = random.Random(f"{args.seed}:{_qid}").random() < 0.5
                return judge.judge(_it["question"], s["answer"], b["answer"], swap)

            r = run_task(f"qa:{qid}:judge", run_judge, partial_path, done)
            if r:
                logger.info("[judge %d/%d] %s -> %s", i, len(qa_items), qid, r.get("verdict"))

        # ---------- 汇总 ----------
        meta = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mcq_per_layer": args.mcq_count,
            "mcq_total": len(mcq_items),
            "qa_count": len(qa_items),
            "baseline_model": settings.zhipuai_model,
            "judge_model": judge_model,
            "seed": args.seed,
            "layers": layers,
        }
        results = aggregate(mcq_items, qa_items, done, meta)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("汇总结果已写入: %s", output_path)
        logger.info("MCQ 准确率: baseline=%.4f system=%.4f | QA 覆盖度: baseline=%.4f system=%.4f",
                    results["baseline"]["mcq_accuracy"], results["system"]["mcq_accuracy"],
                    results["baseline"]["qa_coverage"], results["system"]["qa_coverage"])

        if args.report:
            generate_report(results, load_feedback_stats(), Path(args.report))
    finally:
        agent.close()


if __name__ == "__main__":
    main()
