"""
选择题服务 — MCQ 的 CRUD 和查询
"""

import uuid
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

VALID_LAYERS = {"物理层", "数据链路层", "网络层", "传输层", "应用层"}
VALID_DIFFICULTIES = {"basic", "medium", "hard"}


class QuizService:
    def __init__(self, driver):
        self._driver = driver

    def get_questions(
        self,
        layer: Optional[str] = None,
        difficulty: Optional[str] = None,
        chapter: Optional[str] = None,
        limit: int = 20,
    ) -> Dict:
        conditions = []
        params: Dict = {"limit": min(limit, 100)}

        if layer and layer in VALID_LAYERS:
            conditions.append("q.layer = $layer")
            params["layer"] = layer
        if difficulty and difficulty in VALID_DIFFICULTIES:
            conditions.append("q.difficulty = $difficulty")
            params["difficulty"] = difficulty
        if chapter:
            conditions.append("q.chapter = $chapter")
            params["chapter"] = chapter

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._driver.session() as session:
            total_result = session.run("MATCH (q:MCQuestion) RETURN count(q) AS cnt")
            total = total_result.single()["cnt"]

            result = session.run(
                f"""
                MATCH (q:MCQuestion)
                {where}
                RETURN q.id AS id, q.text AS text,
                       q.option_a AS option_a, q.option_b AS option_b,
                       q.option_c AS option_c, q.option_d AS option_d,
                       q.difficulty AS difficulty, q.layer AS layer,
                       q.chapter AS chapter, q.section AS section
                ORDER BY rand()
                LIMIT $limit
                """,
                **params,
            )
            questions = [dict(record) for record in result]

        return {"questions": questions, "total": total, "filtered": len(questions)}

    def get_chapter_structure(self) -> List[dict]:
        """返回按教材章节组织的题目结构（含每节题目数量）"""
        chapters = [
            {"chapter": "1", "title": "概述", "sections": [
                {"chapter": "1.1", "section": "计算机网络在信息时代中的作用"},
                {"chapter": "1.2", "section": "互联网概述"},
                {"chapter": "1.3", "section": "互联网的组成"},
                {"chapter": "1.5", "section": "计算机网络的类别"},
                {"chapter": "1.6", "section": "计算机网络的性能"},
                {"chapter": "1.7", "section": "计算机网络体系结构"},
            ]},
            {"chapter": "2", "title": "物理层", "sections": [
                {"chapter": "2.1", "section": "物理层的基本概念"},
                {"chapter": "2.2", "section": "数据通信的基础知识"},
                {"chapter": "2.3", "section": "物理层下面的传输媒体"},
                {"chapter": "2.4", "section": "信道复用技术"},
                {"chapter": "2.5", "section": "数字传输系统"},
                {"chapter": "2.6", "section": "宽带接入技术"},
            ]},
            {"chapter": "3", "title": "数据链路层", "sections": [
                {"chapter": "3.1", "section": "使用点对点信道的数据链路层"},
                {"chapter": "3.2", "section": "点对点协议 PPP"},
                {"chapter": "3.3", "section": "使用广播信道的数据链路层"},
                {"chapter": "3.4", "section": "扩展的以太网"},
                {"chapter": "3.5", "section": "高速以太网"},
            ]},
            {"chapter": "4", "title": "网络层", "sections": [
                {"chapter": "4.1", "section": "网络层提供的两种服务"},
                {"chapter": "4.2", "section": "网际协议 IP"},
                {"chapter": "4.3", "section": "划分子网和构造超网"},
                {"chapter": "4.4", "section": "网际控制报文协议 ICMP"},
                {"chapter": "4.5", "section": "互联网的路由选择协议"},
                {"chapter": "4.6", "section": "IPv6"},
                {"chapter": "4.7", "section": "IP 多播"},
                {"chapter": "4.8", "section": "虚拟专用网 VPN 和网络地址转换 NAT"},
            ]},
            {"chapter": "5", "title": "运输层", "sections": [
                {"chapter": "5.1", "section": "运输层协议概述"},
                {"chapter": "5.2", "section": "用户数据报协议 UDP"},
                {"chapter": "5.3", "section": "传输控制协议 TCP 概述"},
                {"chapter": "5.4", "section": "可靠传输的工作原理"},
                {"chapter": "5.5", "section": "TCP 报文段的首部格式"},
                {"chapter": "5.6", "section": "TCP 可靠传输的实现"},
                {"chapter": "5.7", "section": "TCP 的流量控制"},
                {"chapter": "5.8", "section": "TCP 的拥塞控制"},
                {"chapter": "5.9", "section": "TCP 的运输连接管理"},
            ]},
            {"chapter": "6", "title": "应用层", "sections": [
                {"chapter": "6.1", "section": "域名系统 DNS"},
                {"chapter": "6.2", "section": "文件传送协议"},
                {"chapter": "6.3", "section": "远程终端协议 TELNET"},
                {"chapter": "6.4", "section": "万维网 WWW"},
                {"chapter": "6.5", "section": "电子邮件"},
                {"chapter": "6.6", "section": "动态主机配置协议 DHCP"},
                {"chapter": "6.7", "section": "简单网络管理协议 SNMP"},
                {"chapter": "6.8", "section": "应用进程跨越网络的通信"},
                {"chapter": "6.9", "section": "P2P 应用"},
            ]},
        ]

        with self._driver.session() as session:
            # 查询每个 chapter 的题目数量
            counts = {}
            for r in session.run(
                "MATCH (q:MCQuestion) RETURN q.chapter AS chapter, count(q) AS cnt"
            ):
                ch = r["chapter"] or ""
                counts[ch] = r["cnt"]

            # 填充数量
            for ch_group in chapters:
                total = 0
                for sec in ch_group["sections"]:
                    sec["count"] = counts.get(sec["chapter"], 0)
                    total += sec["count"]
                ch_group["count"] = total

        return chapters

    def check_answer(self, question_id: str) -> Optional[Dict]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (q:MCQuestion {id: $id})
                OPTIONAL MATCH (q)-[:ABOUT]->(e)
                RETURN q.correct_answer AS correct_answer,
                       q.explanation AS explanation,
                       q.layer AS layer,
                       q.difficulty AS difficulty,
                       collect(e.name) AS references
                """,
                id=question_id,
            )
            record = result.single()
            if not record:
                return None
            return {
                "correct_answer": record["correct_answer"],
                "explanation": record["explanation"],
                "layer": record["layer"],
                "difficulty": record["difficulty"],
                "references": [r for r in (record["references"] or []) if r],
            }

    def get_stats(self) -> Dict:
        with self._driver.session() as session:
            total = session.run(
                "MATCH (q:MCQuestion) RETURN count(q) AS cnt"
            ).single()["cnt"]

            by_layer = {}
            for r in session.run(
                "MATCH (q:MCQuestion) RETURN q.layer AS layer, count(q) AS cnt"
            ):
                by_layer[r["layer"]] = r["cnt"]

            by_difficulty = {}
            for r in session.run(
                "MATCH (q:MCQuestion) RETURN q.difficulty AS difficulty, count(q) AS cnt"
            ):
                by_difficulty[r["difficulty"]] = r["cnt"]

            by_chapter = {}
            for r in session.run(
                "MATCH (q:MCQuestion) WHERE q.chapter IS NOT NULL RETURN q.chapter AS chapter, count(q) AS cnt"
            ):
                by_chapter[r["chapter"]] = r["cnt"]

        return {
            "total_questions": total,
            "by_layer": by_layer,
            "by_difficulty": by_difficulty,
            "by_chapter": by_chapter,
        }

    def add_question(self, data: Dict) -> str:
        q_id = f"mcq_{uuid.uuid4().hex[:8]}"
        with self._driver.session() as session:
            session.run(
                """
                MERGE (q:MCQuestion {id: $id})
                SET q.text = $text,
                    q.option_a = $option_a, q.option_b = $option_b,
                    q.option_c = $option_c, q.option_d = $option_d,
                    q.correct_answer = $correct_answer,
                    q.explanation = $explanation,
                    q.difficulty = $difficulty,
                    q.layer = $layer,
                    q.chapter = $chapter
                """,
                id=q_id,
                text=data["text"],
                option_a=data["option_a"],
                option_b=data["option_b"],
                option_c=data["option_c"],
                option_d=data["option_d"],
                correct_answer=data["correct_answer"],
                explanation=data["explanation"],
                difficulty=data["difficulty"],
                layer=data["layer"],
                chapter=data.get("chapter", ""),
            )

            session.run(
                """
                MATCH (q:MCQuestion {id: $id})
                MATCH (l:Layer {name: $layer})
                MERGE (q)-[:ABOUT]->(l)
                """,
                id=q_id,
                layer=data["layer"],
            )

            for ref in data.get("references", []):
                session.run(
                    """
                    MATCH (q:MCQuestion {id: $id})
                    MATCH (e {name: $ref})
                    MERGE (q)-[:ABOUT]->(e)
                    """,
                    id=q_id,
                    ref=ref.strip(),
                )

        return q_id

    def import_batch(self, questions: List[Dict]) -> Dict:
        imported = 0
        failed = 0
        for q in questions:
            try:
                self.add_question(q)
                imported += 1
            except Exception as e:
                logger.warning("导入题目失败", text=q.get("text", "")[:50], error=str(e))
                failed += 1
        return {"imported": imported, "failed": failed}

    def create_indexes(self):
        with self._driver.session() as session:
            for idx in [
                "CREATE INDEX mcq_id_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.id)",
                "CREATE INDEX mcq_layer_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.layer)",
                "CREATE INDEX mcq_difficulty_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.difficulty)",
            ]:
                try:
                    session.run(idx)
                except Exception as e:
                    logger.warning("创建索引失败", error=str(e))
