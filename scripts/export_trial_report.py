#!/usr/bin/env python3
"""
导出用户试用评估报告（前测-后测试用评估数据 → docs/报告/用户试用评估报告.md）

用法：
    python scripts/export_trial_report.py --trial-id trial_xxxx     # 指定批次导出
    python scripts/export_trial_report.py --list                   # 列出全部批次

说明：
    报告 = 数据表格（analysis 汇总，本地生成、不依赖 LLM） + LLM 生成的评估叙述。
    服务器运行目录未必可写 docs/，因此以研究者本地运行脚本的方式产出结题交付物。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("export_trial_report")

OUTPUT_PATH = project_root / "docs" / "报告" / "用户试用评估报告.md"


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def pp(v):
    if v is None:
        return "—"
    v = v * 100
    return f"+{v:.1f} pp" if v > 0 else f"{v:.1f} pp"


def build_data_section(analysis: dict) -> str:
    agg = analysis["aggregate"]
    lines = []
    lines.append(f"## 数据附录（批次 {analysis['trial_name']}，自动生成）\n")
    lines.append(f"- 批次 ID：`{analysis['trial_id']}`，创建于 {analysis['created_at']}")
    lines.append(
        f"- 试卷：每层 {analysis['config']['per_layer']} 题 × 5 层 = "
        f"{analysis['config']['question_count']} 题，前测/后测平行卷（seed={analysis['config']['seed']}）"
    )
    lines.append(f"- 有效被试（完成前后测）：{agg['n']} 人\n")

    if agg["n"] > 0:
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        lines.append(f"| 前测平均正确率 | {pct(agg['mean_pre'])} |")
        lines.append(f"| 后测平均正确率 | {pct(agg['mean_post'])} |")
        lines.append(f"| 平均提升 | {pp(agg['mean_gain'])}（标准差 {pp(agg['std_gain'])}） |")
        lines.append(f"| 提升率（后测提高占比） | {pct(agg['improvement_rate'])} |")
        lines.append("")

        layer_gain = agg.get("layer_gain") or {}
        if layer_gain:
            lines.append("| 层次 | 前测均值 | 后测均值 | 提升 |")
            lines.append("| --- | --- | --- | --- |")
            for layer, g in layer_gain.items():
                lines.append(
                    f"| {layer} | {pct(g['mean_pre'])} | {pct(g['mean_post'])} | {pp(g['mean_gain'])} |"
                )
            lines.append("")

    completed = [p for p in analysis["participants"] if p["completed"]]
    if completed:
        lines.append("| 参试码 | 前测 | 后测 | 提升 | 答题 | 复习 | 覆盖知识点 | 满意度 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for p in completed:
            quiz = ((p["behavior"] or {}).get("by_type") or {}) if p["behavior"] else {}
            quiz_n = (quiz.get("quiz_correct") or {}).get("count", 0) + (quiz.get("quiz_wrong") or {}).get("count", 0)
            manual_n = (quiz.get("manual") or {}).get("count", 0)
            entities = (p["behavior"] or {}).get("total_entities", 0) if p["behavior"] else 0
            sat = p["satisfaction"]
            sat_text = f"{sat['positive']}/{sat['total']}" if sat and sat["total"] else "—"
            lines.append(
                f"| {p['code']} | {pct(p['pre']['accuracy'])} | {pct(p['post']['accuracy'])} | "
                f"{pp(p['gain'])} | {quiz_n} | {manual_n} | {entities} | {sat_text} |"
            )
        lines.append("")

    pending = [p for p in analysis["participants"] if not p["completed"]]
    if pending:
        codes = "、".join(p["code"] for p in pending)
        lines.append(f"未完成双测被试（不计入统计）：{codes}\n")

    if agg.get("small_sample_caveat"):
        lines.append("> 注意：有效样本量 n < 5，以上结果仅为初步观察，不具统计显著性。\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="导出用户试用评估报告")
    parser.add_argument("--trial-id", help="试用批次 ID（见 --list）")
    parser.add_argument("--list", action="store_true", help="列出全部批次")
    args = parser.parse_args()

    from neo4j import GraphDatabase

    from src.mastery_tracker import MasteryTracker
    from src.services.trial_service import TrialService
    from src.settings import get_settings

    settings = get_settings()
    db_path = project_root / "data" / "memory" / "mastery.db"
    tracker = MasteryTracker(db_path=str(db_path))
    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    service = TrialService(driver, tracker)

    try:
        if args.list or not args.trial_id:
            trials = service.list_trials()
            if not trials:
                logger.info("暂无试用批次，请先在 /trial 页面创建")
                return
            for t in trials:
                print(f"{t['id']}  被试 {t['participant_count']} 交卷 {t['submitted_test_count']}  {t['name']}")
            return

        analysis = service.get_analysis(args.trial_id)
        logger.info("批次 %s：有效被试 %s 人", args.trial_id, analysis["aggregate"]["n"])

        logger.info("调用 LLM 生成评估叙述...")
        narrative = service.generate_report(args.trial_id)
        if not narrative:
            logger.warning("LLM 报告生成失败，仅导出数据附录")

        data_section = build_data_section(analysis)
        header = "> 本报告由 `scripts/export_trial_report.py` 基于前测-后测试用评估数据自动生成。\n\n"
        if narrative:
            content = f"{header}{narrative}\n\n---\n\n{data_section}\n"
        else:
            content = (
                f"# 用户试用评估报告\n\n{header}"
                f"（LLM 叙述生成失败，可重试）\n\n---\n\n{data_section}\n"
            )

        OUTPUT_PATH.write_text(content, encoding="utf-8")
        logger.info("已写入 %s", OUTPUT_PATH)
    finally:
        tracker.close()
        driver.close()


if __name__ == "__main__":
    main()
