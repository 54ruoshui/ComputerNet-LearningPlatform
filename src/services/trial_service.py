"""
试用评估服务 — 前测-后测实验设计：批次管理、平行卷生成、批改、效果分析与 LLM 评估报告
"""

import json
import random
import statistics
import uuid
from typing import Dict, List, Optional

import structlog
from openai import OpenAI

from src.settings import get_settings

logger = structlog.get_logger(__name__)

# 与 quiz_service.VALID_LAYERS 一致，固定顺序保证抽卷稳定
MCQ_LAYERS = ["物理层", "数据链路层", "网络层", "传输层", "应用层"]

# 被试码字符表：去掉易混淆字符（0/O、1/I/L），6 位约 8.9 亿组合，防枚举爆破
# （存量的 4 位纯数字旧码按字符串精确匹配，仍可正常加入）
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 6


class TrialService:
    def __init__(self, driver, tracker):
        self._driver = driver
        self._tracker = tracker

    # ==================== 批次管理 ====================

    def create_trial(self, name: str, per_layer: int = 4, seed: Optional[int] = None) -> dict:
        """创建试用批次并生成分层平行卷（前测/后测互斥、同层同量、seed 可复现）"""
        pools = self._fetch_layer_pools()
        if seed is None:
            seed = random.randint(0, 999999)
        rng = random.Random(seed)

        pre_ids: List[str] = []
        post_ids: List[str] = []
        insufficient = []
        for layer in MCQ_LAYERS:
            ids = pools.get(layer, [])
            need = per_layer * 2
            if len(ids) < need:
                insufficient.append(f"{layer} 仅 {len(ids)} 题（需 {need}）")
                continue
            sampled = rng.sample(ids, need)
            rng.shuffle(sampled)
            pre_ids.extend(sampled[:per_layer])
            post_ids.extend(sampled[per_layer:])

        if insufficient:
            raise ValueError("题库不足，无法生成平行卷: " + "; ".join(insufficient))

        trial_id = f"trial_{uuid.uuid4().hex[:8]}"
        config = {
            "per_layer": per_layer,
            "seed": seed,
            "layers": MCQ_LAYERS,
            "pre_ids": pre_ids,
            "post_ids": post_ids,
        }
        self._tracker.save_trial(trial_id, name, config)
        logger.info(
            "试用批次已创建", trial_id=trial_id, name=name,
            per_layer=per_layer, seed=seed, question_count=len(pre_ids),
        )
        return {
            "trial_id": trial_id, "name": name, "per_layer": per_layer, "seed": seed,
            "question_count": len(pre_ids), "pre_ids": pre_ids, "post_ids": post_ids,
        }

    def _fetch_layer_pools(self) -> Dict[str, List[str]]:
        pools: Dict[str, List[str]] = {layer: [] for layer in MCQ_LAYERS}
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (q:MCQuestion) WHERE q.layer IN $layers
                RETURN q.layer AS layer, collect(q.id) AS ids
                """,
                layers=list(MCQ_LAYERS),
            )
            for record in result:
                pools[record["layer"]] = record["ids"]
        return pools

    def list_trials(self) -> list:
        return self._tracker.list_trials()

    def list_participants(self, trial_id: str) -> list:
        trial = self._tracker.get_trial(trial_id)
        if not trial:
            raise ValueError(f"试用批次 {trial_id} 不存在")
        return self._tracker.list_participants(trial_id)

    def add_participant(self, trial_id: str, code: Optional[str] = None) -> dict:
        trial = self._tracker.get_trial(trial_id)
        if not trial:
            raise ValueError(f"试用批次 {trial_id} 不存在")
        if not code:
            code = self._generate_code(trial_id)
        return self._tracker.add_trial_participant(trial_id, code)

    def _generate_code(self, trial_id: str) -> str:
        existing = {p["code"] for p in self._tracker.list_participants(trial_id)}
        while True:
            code = "P" + "".join(random.choices(CODE_ALPHABET, k=CODE_LENGTH))
            if code not in existing:
                return code

    def join(self, code: str, session_id: str) -> dict:
        """被试凭码加入；协商学习会话归属后返回前后测完成状态。

        - 请求会话为空/"default"，或已被其他被试占用（共用电脑）→ 服务端另发新会话
        - 被试此前已绑定会话（换设备回来）→ 不改绑，返回原会话（历史延续）
        - 响应顶层 session_id 为客户端应使用的会话
        """
        participant = self._tracker.get_participant_by_code(code)
        if not participant:
            raise ValueError(f"被试码 {code} 无效")

        effective = session_id
        # 旧版本可能把被试绑到共享的 "default" 会话，视为未绑定重新协商
        bound = participant["session_id"]
        if bound and bound != "default":
            effective = bound
        else:
            usable = bool(session_id) and session_id != "default"
            if usable:
                owner = self._tracker.get_participant_by_session(session_id)
                if owner and owner["id"] != participant["id"]:
                    usable = False
            if not usable:
                effective = str(uuid.uuid4())
            self._tracker.bind_participant_session(participant["id"], effective)
            participant["session_id"] = effective

        status = self._participant_status(participant)
        return {"participant": participant, "session_id": effective, **status}

    def _participant_status(self, participant: dict) -> dict:
        pre_done = post_done = False
        for test in self._tracker.get_trial_tests(participant["trial_id"]):
            if test["participant_id"] != participant["id"]:
                continue
            if test["phase"] == "pre":
                pre_done = test["submitted_at"] is not None
            elif test["phase"] == "post":
                post_done = test["submitted_at"] is not None
        return {"pre_done": pre_done, "post_done": post_done}

    # ==================== 测试作答 ====================

    def start_test(self, code: str, phase: str) -> dict:
        """开始前测/后测，返回试卷题目（不含答案）。后测需前测已提交"""
        if phase not in ("pre", "post"):
            raise ValueError("phase 必须是 pre 或 post")
        participant = self._tracker.get_participant_by_code(code)
        if not participant:
            raise ValueError(f"被试码 {code} 无效")

        trial = self._tracker.get_trial(participant["trial_id"])
        config = trial["config"]
        if phase == "post" and not self._participant_status(participant)["pre_done"]:
            raise ValueError("前测尚未提交，不能开始后测")

        question_ids = config.get(f"{phase}_ids", [])
        if not question_ids:
            raise ValueError("批次试卷配置缺失")

        upserted = self._tracker.upsert_trial_test(
            participant["trial_id"], participant["id"], phase, question_ids
        )
        questions = self._fetch_questions(upserted["question_ids"])
        return {
            "test_id": upserted["test_id"],
            "trial_id": participant["trial_id"],
            "code": code,
            "phase": phase,
            "question_count": len(questions),
            "questions": questions,
        }

    def _fetch_questions(self, question_ids: List[str], with_answers: bool = False) -> list:
        fields = (
            "q.id AS id, q.text AS text, q.option_a AS option_a, q.option_b AS option_b, "
            "q.option_c AS option_c, q.option_d AS option_d, q.layer AS layer, "
            "q.difficulty AS difficulty, q.chapter AS chapter"
        )
        if with_answers:
            fields += ", q.correct_answer AS correct_answer, q.explanation AS explanation"
        with self._driver.session() as session:
            result = session.run(
                f"MATCH (q:MCQuestion) WHERE q.id IN $ids RETURN {fields}", ids=question_ids
            )
            by_id = {record["id"]: dict(record) for record in result}
        # 按试卷既定顺序返回，缺题跳过
        return [by_id[qid] for qid in question_ids if qid in by_id]

    def submit_test(self, test_id: int, answers: Dict[str, str], code: str) -> dict:
        test = self._tracker.get_trial_test(test_id)
        if not test:
            raise ValueError(f"测试记录 {test_id} 不存在")
        if test["submitted_at"]:
            raise ValueError("该测试已提交，不可重复提交")
        # test_id 是自增整数可枚举，必须校验提交者凭被试码与归属被试一致，防替他人交卷
        participant = self._tracker.get_participant_by_code(code)
        if not participant or participant["id"] != test["participant_id"]:
            raise ValueError("被试码与该测试不匹配，无权提交")

        questions = self._fetch_questions(test["question_ids"], with_answers=True)
        details = []
        correct_count = 0
        for q in questions:
            selected = answers.get(q["id"], "")
            is_correct = selected == q.get("correct_answer", "")
            correct_count += 1 if is_correct else 0
            details.append({
                "question_id": q["id"],
                "text": q.get("text", ""),
                "selected_answer": selected,
                "correct_answer": q.get("correct_answer", ""),
                "correct": is_correct,
                "layer": q.get("layer", ""),
                "difficulty": q.get("difficulty", ""),
                "explanation": q.get("explanation", ""),
            })
        total = len(questions)

        self._tracker.save_trial_test_submit(test_id, answers, correct_count, total, details)
        return {
            "test_id": test_id,
            "phase": test["phase"],
            "correct_count": correct_count,
            "total_count": total,
            "accuracy": round(correct_count / total, 4) if total else 0.0,
            "details": details,
        }

    # ==================== 效果分析 ====================

    def get_analysis(self, trial_id: str) -> dict:
        trial = self._tracker.get_trial(trial_id)
        if not trial:
            raise ValueError(f"试用批次 {trial_id} 不存在")

        participants = self._tracker.list_participants(trial_id)
        tests = self._tracker.get_trial_tests(trial_id)
        tests_by_participant: Dict[int, Dict[str, dict]] = {}
        for t in tests:
            tests_by_participant.setdefault(t["participant_id"], {})[t["phase"]] = t

        completed, pending = [], []
        for p in participants:
            entry = self._build_participant_entry(p, tests_by_participant.get(p["id"], {}))
            if entry["completed"]:
                completed.append(entry)
            else:
                pending.append(entry)

        layers = trial["config"].get("layers", [])
        return {
            "trial_id": trial_id,
            "trial_name": trial["name"],
            "created_at": trial["created_at"],
            "config": {
                "per_layer": trial["config"].get("per_layer"),
                "seed": trial["config"].get("seed"),
                "question_count": len(trial["config"].get("pre_ids", [])),
            },
            "layers": layers,
            "participants": completed + pending,
            "aggregate": self._aggregate(completed, layers),
        }

    def _build_participant_entry(self, participant: dict, phase_tests: dict) -> dict:
        pre, post = phase_tests.get("pre"), phase_tests.get("post")
        completed = bool(
            pre and post and pre["submitted_at"] and post["submitted_at"]
        )
        entry = {
            "code": participant["code"],
            "session_id": participant["session_id"],
            "bound": bool(participant["session_id"]),
            "completed": completed,
            "pre": self._test_summary(pre),
            "post": self._test_summary(post),
            "gain": None,
            "layer_gain": {},
            "behavior": None,
            "satisfaction": None,
        }
        if completed:
            entry["gain"] = round(entry["post"]["accuracy"] - entry["pre"]["accuracy"], 4)
            entry["layer_gain"] = self._layer_gains(pre["details"], post["details"])
        if participant["session_id"]:
            entry["behavior"] = self._tracker.get_review_behavior(participant["session_id"])
            entry["satisfaction"] = self._tracker.get_feedback_stats(participant["session_id"])
        return entry

    @staticmethod
    def _test_summary(test: Optional[dict]) -> Optional[dict]:
        if not test:
            return None
        if not test["submitted_at"]:
            return {"status": "started", "submitted_at": None}
        total = test["total_count"]
        return {
            "status": "submitted",
            "submitted_at": test["submitted_at"],
            "correct_count": test["correct_count"],
            "total_count": total,
            "accuracy": round(test["correct_count"] / total, 4) if total else 0.0,
        }

    @staticmethod
    def _layer_gains(pre_details: list, post_details: list) -> Dict[str, dict]:
        def by_layer(details):
            stats: Dict[str, dict] = {}
            for d in details:
                layer = d.get("layer") or "未知"
                s = stats.setdefault(layer, {"correct": 0, "total": 0})
                s["total"] += 1
                s["correct"] += 1 if d.get("correct") else 0
            return stats

        pre, post = by_layer(pre_details), by_layer(post_details)
        gains = {}
        for layer in {**pre, **post}:
            p, q = pre.get(layer, {"correct": 0, "total": 0}), post.get(layer, {"correct": 0, "total": 0})
            p_acc = p["correct"] / p["total"] if p["total"] else 0.0
            q_acc = q["correct"] / q["total"] if q["total"] else 0.0
            gains[layer] = {
                "pre_accuracy": round(p_acc, 4),
                "post_accuracy": round(q_acc, 4),
                "gain": round(q_acc - p_acc, 4),
            }
        return gains

    @staticmethod
    def _aggregate(completed: list, layers: list) -> dict:
        if not completed:
            return {
                "n": 0, "note": "暂无完成前后测的被试",
                "mean_pre": None, "mean_post": None, "mean_gain": None,
                "std_gain": None, "improvement_rate": None, "layer_gain": {},
            }
        pre_scores = [p["pre"]["accuracy"] for p in completed]
        post_scores = [p["post"]["accuracy"] for p in completed]
        gains = [p["gain"] for p in completed]
        layer_gain: Dict[str, dict] = {}
        for layer in layers:
            samples = [p["layer_gain"].get(layer) for p in completed if layer in p.get("layer_gain", {})]
            if samples:
                layer_gain[layer] = {
                    "mean_pre": round(statistics.mean(s["pre_accuracy"] for s in samples), 4),
                    "mean_post": round(statistics.mean(s["post_accuracy"] for s in samples), 4),
                    "mean_gain": round(statistics.mean(s["gain"] for s in samples), 4),
                }
        return {
            "n": len(completed),
            "mean_pre": round(statistics.mean(pre_scores), 4),
            "mean_post": round(statistics.mean(post_scores), 4),
            "mean_gain": round(statistics.mean(gains), 4),
            "std_gain": round(statistics.stdev(gains), 4) if len(gains) > 1 else 0.0,
            "improvement_rate": round(
                sum(1 for g in gains if g > 0) / len(gains), 4
            ),
            "layer_gain": layer_gain,
            "small_sample_caveat": len(completed) < 5,
        }

    # ==================== LLM 评估报告 ====================

    def generate_report(self, trial_id: str) -> Optional[str]:
        analysis = self.get_analysis(trial_id)
        prompt = self._build_report_prompt(analysis)
        settings = get_settings()
        try:
            client = OpenAI(
                api_key=settings.zhipuai_api_key,
                base_url=settings.zhipuai_base_url,
            )
            response = client.chat.completions.create(
                model=settings.zhipuai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2048,
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            return content.strip()
        except Exception as e:
            logger.warning("LLM 试用评估报告生成失败", error=str(e))
            return None

    @staticmethod
    def _build_report_prompt(analysis: dict) -> str:
        agg = analysis["aggregate"]
        return f"""你是一位教育技术研究者，请基于以下前测-后测试用评估数据，撰写一份《用户试用评估报告》正文（Markdown 格式，中文）。

要求：
1. 包含小节：一、试用概况；二、学习效果分析（整体与分层）；三、学习行为分析；四、满意度分析；五、结论与建议。
2. 正确率用百分比表述，客观呈现数据，不过度美化。
3. n={agg['n']} 是有效样本量，若样本较小（n<5）必须在显著位置声明结果仅为初步观察、不具备统计显著性。
4. 若有未完成双测或未绑定会话的被试，在概况中说明。
5. 直接输出正文，不要额外寒暄。

试用批次：{analysis['trial_name']}（{analysis['trial_id']}）
试卷：每层 {analysis['config']['per_layer']} 题 × 5 层 = {analysis['config']['question_count']} 题，前测/后测平行卷
整体统计：{json.dumps({k: agg.get(k) for k in ('n', 'mean_pre', 'mean_post', 'mean_gain', 'std_gain', 'improvement_rate')}, ensure_ascii=False)}
分层统计：{json.dumps(agg.get('layer_gain', {}), ensure_ascii=False)}
被试明细（含学习行为与满意度）：{json.dumps(analysis['participants'], ensure_ascii=False)}"""
