"""
质量监控系统 - 实时监控和评估知识图谱质量
包括多维度的质量指标、异常检测和趋势分析
"""

import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import statistics
from neo4j import GraphDatabase
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class QualityMetrics:
    """质量指标数据类"""
    timestamp: str
    extraction_accuracy: float  # 提取准确率
    validation_pass_rate: float  # 验证通过率
    confidence_score: float  # 平均置信度
    knowledge_consistency: float  # 知识一致性
    relationship_correctness: float  # 关系正确性
    user_satisfaction: float  # 用户满意度
    response_time: float  # 响应时间（秒）
    error_rate: float  # 错误率
    hallucination_risk: float  # 幻觉风险

@dataclass
class QualityAlert:
    """质量告警数据类"""
    alert_id: str
    severity: str  # 'critical', 'warning', 'info'
    metric_name: str
    current_value: float
    threshold: float
    message: str
    timestamp: str
    resolved: bool = False
    resolution_note: str = ""

@dataclass
class QualityTrend:
    """质量趋势数据类"""
    metric_name: str
    period: str  # 'hour', 'day', 'week'
    trend: str  # 'improving', 'stable', 'declining'
    current_value: float
    previous_value: float
    change_rate: float
    data_points: List[float] = field(default_factory=list)

class QualityMonitor:
    """质量监控器 - 实时监控系统质量"""

    def __init__(self, neo4j_uri: str, neo4j_auth: Tuple[str, str]):
        """
        初始化质量监控器

        Args:
            neo4j_uri: Neo4j连接URI
            neo4j_auth: Neo4j认证信息 (username, password)
        """
        self.neo4j_uri = neo4j_uri
        self.neo4j_auth = neo4j_auth
        self.driver = None

        # 质量指标历史数据
        self.metrics_history: List[QualityMetrics] = []
        self.max_history_size = 1000

        # 告警系统
        self.alerts: List[QualityAlert] = []
        self.alert_id_counter = 0

        # 质量阈值配置
        self.thresholds = {
            'extraction_accuracy': 0.85,
            'validation_pass_rate': 0.80,
            'confidence_score': 0.75,
            'knowledge_consistency': 0.90,
            'relationship_correctness': 0.85,
            'user_satisfaction': 0.80,
            'response_time': 5.0,  # 秒
            'error_rate': 0.05,
            'hallucination_risk': 0.30
        }

        # 连接Neo4j
        self._connect()

    def _connect(self):
        """连接到Neo4j数据库"""
        try:
            self.driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=self.neo4j_auth
            )
            logger.info("✅ 质量监控系统连接Neo4j成功")
        except Exception as e:
            logger.error(f"❌ 连接Neo4j失败: {e}")
            raise

    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            logger.info("质量监控系统数据库连接已关闭")

    def collect_metrics(self, extraction_results: Dict[str, Any],
                       validation_results: Dict[str, Any],
                       performance_data: Dict[str, Any] = None) -> QualityMetrics:
        """
        收集当前的质量指标

        Args:
            extraction_results: 提取结果
            validation_results: 验证结果
            performance_data: 性能数据

        Returns:
            质量指标对象
        """
        try:
            # 1. 计算提取准确率
            extraction_accuracy = self._calculate_extraction_accuracy(extraction_results)

            # 2. 计算验证通过率
            validation_pass_rate = self._calculate_validation_pass_rate(validation_results)

            # 3. 计算平均置信度
            confidence_score = self._calculate_average_confidence(extraction_results, validation_results)

            # 4. 计算知识一致性
            knowledge_consistency = self._calculate_knowledge_consistency()

            # 5. 计算关系正确性
            relationship_correctness = self._calculate_relationship_correctness(extraction_results)

            # 6. 获取用户满意度（从反馈数据）
            user_satisfaction = self._get_user_satisfaction()

            # 7. 获取性能数据
            response_time = performance_data.get('response_time', 0) if performance_data else 0
            error_rate = performance_data.get('error_rate', 0) if performance_data else 0

            # 8. 计算幻觉风险
            hallucination_risk = self._calculate_hallucination_risk(validation_results)

            # 创建质量指标对象
            metrics = QualityMetrics(
                timestamp=datetime.now().isoformat(),
                extraction_accuracy=extraction_accuracy,
                validation_pass_rate=validation_pass_rate,
                confidence_score=confidence_score,
                knowledge_consistency=knowledge_consistency,
                relationship_correctness=relationship_correctness,
                user_satisfaction=user_satisfaction,
                response_time=response_time,
                error_rate=error_rate,
                hallucination_risk=hallucination_risk
            )

            # 保存到历史记录
            self.metrics_history.append(metrics)
            if len(self.metrics_history) > self.max_history_size:
                self.metrics_history.pop(0)

            # 检查阈值并生成告警
            self._check_thresholds(metrics)

            logger.info(f"📊 质量指标收集完成: 准确率={extraction_accuracy:.2f}, "
                       f"验证通过率={validation_pass_rate:.2f}, "
                       f"置信度={confidence_score:.2f}")

            return metrics

        except Exception as e:
            logger.error(f"收集质量指标失败: {e}")
            raise

    def _calculate_extraction_accuracy(self, extraction_results: Dict[str, Any]) -> float:
        """计算提取准确率"""
        try:
            keywords = extraction_results.get('keywords', [])
            relationships = extraction_results.get('relationships', [])

            if not keywords and not relationships:
                return 0.5  # 无数据时的默认值

            # 基于置信度计算准确率
            keyword_scores = [kw.get('confidence', 0) for kw in keywords]
            relationship_scores = [rel.get('confidence', 0) for rel in relationships]

            all_scores = keyword_scores + relationship_scores
            if not all_scores:
                return 0.5

            return sum(all_scores) / len(all_scores)

        except Exception as e:
            logger.error(f"计算提取准确率失败: {e}")
            return 0.5

    def _calculate_validation_pass_rate(self, validation_results: Dict[str, Any]) -> float:
        """计算验证通过率"""
        try:
            total_validations = validation_results.get('total_validations', 0)
            validity_rate = validation_results.get('validity_rate', 0.5)

            return validity_rate if total_validations > 0 else 0.5

        except Exception as e:
            logger.error(f"计算验证通过率失败: {e}")
            return 0.5

    def _calculate_average_confidence(self, extraction_results: Dict[str, Any],
                                      validation_results: Dict[str, Any]) -> float:
        """计算平均置信度"""
        try:
            # 从验证结果获取平均置信度
            avg_validation_confidence = validation_results.get('average_confidence', 0.5)

            # 从提取结果获取平均置信度
            keywords = extraction_results.get('keywords', [])
            relationships = extraction_results.get('relationships', [])

            keyword_scores = [kw.get('confidence', 0) for kw in keywords]
            relationship_scores = [rel.get('confidence', 0) for rel in relationships]

            all_scores = keyword_scores + relationship_scores

            if not all_scores:
                return avg_validation_confidence

            avg_extraction_confidence = sum(all_scores) / len(all_scores)

            # 综合两者
            return (avg_extraction_confidence + avg_validation_confidence) / 2

        except Exception as e:
            logger.error(f"计算平均置信度失败: {e}")
            return 0.5

    def _calculate_knowledge_consistency(self) -> float:
        """计算知识一致性（从图谱中检查）"""
        try:
            with self.driver.session() as session:
                # 检查图谱中的不一致关系
                consistency_check = session.run("""
                    // 检查自引用关系（不合理的情况）
                    MATCH (a)-[r]->(a)
                    WHERE a.name IS NOT NULL
                    WITH count(r) as self_ref_count

                    // 检查相互矛盾的关系
                    MATCH (a)-[r1]->(b)
                    MATCH (a)-[r2]->(b)
                    WHERE r1.type <> r2.type
                    WITH self_ref_count, count(r1) as contradictory_count

                    // 获取总关系数
                    MATCH ()-[r]->()
                    WITH self_ref_count, contradictory_count, count(r) as total_relationships

                    // 计算一致性分数
                    RETURN CASE
                        WHEN total_relationships = 0 THEN 1.0
                        ELSE 1.0 - ((self_ref_count + contradictory_count) * 2.0 / total_relationships)
                    END as consistency_score
                """)

                result = consistency_check.single()
                if result:
                    score = result['consistency_score']
                    return max(0.0, min(1.0, score))

                return 0.8  # 默认值

        except Exception as e:
            logger.error(f"计算知识一致性失败: {e}")
            return 0.8

    def _calculate_relationship_correctness(self, extraction_results: Dict[str, Any]) -> float:
        """计算关系正确性"""
        try:
            relationships = extraction_results.get('relationships', [])

            if not relationships:
                return 0.8

            valid_count = 0
            total_count = 0

            for rel in relationships:
                validation = rel.get('validation', {})
                if validation.get('is_valid', False):
                    valid_count += 1
                total_count += 1

            return valid_count / total_count if total_count > 0 else 0.8

        except Exception as e:
            logger.error(f"计算关系正确性失败: {e}")
            return 0.8

    def _get_user_satisfaction(self) -> float:
        """获取用户满意度（从反馈数据）"""
        try:
            with self.driver.session() as session:
                # 从反馈数据计算满意度
                result = session.run("""
                    MATCH (f:Feedback)
                    WHERE f.is_correct IS NOT NULL
                    WITH sum(CASE WHEN f.is_correct = true THEN 1 ELSE 0 END) as positive,
                         count(f) as total
                    RETURN CASE
                        WHEN total = 0 THEN 0.8
                        ELSE positive * 1.0 / total
                    END as satisfaction_score
                """)

                feedback_result = result.single()
                if feedback_result:
                    return feedback_result['satisfaction_score']

                return 0.8

        except Exception as e:
            logger.error(f"获取用户满意度失败: {e}")
            return 0.8

    def _calculate_hallucination_risk(self, validation_results: Dict[str, Any]) -> float:
        """计算幻觉风险"""
        try:
            # 基于验证结果中的语义分析
            validation_entries = validation_results.get('results', [])

            if not validation_entries:
                return 0.3  # 默认中等风险

            # 检查每个验证结果中的幻觉风险指标
            hallucination_scores = []

            for entry in validation_entries:
                result = entry.get('result', {})
                if hasattr(result, 'hallucination_risk'):
                    hallucination_scores.append(result.hallucination_risk)
                elif isinstance(result, dict) and 'hallucination_risk' in result:
                    hallucination_scores.append(result['hallucination_risk'])

            if hallucination_scores:
                return sum(hallucination_scores) / len(hallucination_scores)

            return 0.3  # 默认值

        except Exception as e:
            logger.error(f"计算幻觉风险失败: {e}")
            return 0.3

    def _check_thresholds(self, metrics: QualityMetrics):
        """检查指标阈值并生成告警"""
        metric_dict = asdict(metrics)

        for metric_name, threshold in self.thresholds.items():
            current_value = metric_dict.get(metric_name, 0)

            # 根据指标类型判断是否超过阈值
            should_alert = False
            if metric_name in ['response_time', 'error_rate', 'hallucination_risk']:
                # 这些指标越低越好
                should_alert = current_value > threshold
            else:
                # 这些指标越高越好
                should_alert = current_value < threshold

            if should_alert:
                severity = self._get_alert_severity(metric_name, current_value, threshold)
                self._create_alert(
                    metric_name=metric_name,
                    current_value=current_value,
                    threshold=threshold,
                    severity=severity
                )

    def _get_alert_severity(self, metric_name: str, current_value: float,
                           threshold: float) -> str:
        """获取告警严重程度"""
        # 根据偏离程度判断严重性
        deviation_ratio = abs(current_value - threshold) / threshold

        if deviation_ratio > 0.3:
            return 'critical'
        elif deviation_ratio > 0.15:
            return 'warning'
        else:
            return 'info'

    def _create_alert(self, metric_name: str, current_value: float,
                     threshold: float, severity: str):
        """创建告警"""
        self.alert_id_counter += 1
        alert_id = f"ALT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self.alert_id_counter}"

        # 判断是过高还是过低
        if metric_name in ['response_time', 'error_rate', 'hallucination_risk']:
            message = f"{metric_name} 偏高: {current_value:.2f} (阈值: {threshold:.2f})"
        else:
            message = f"{metric_name} 偏低: {current_value:.2f} (阈值: {threshold:.2f})"

        alert = QualityAlert(
            alert_id=alert_id,
            severity=severity,
            metric_name=metric_name,
            current_value=current_value,
            threshold=threshold,
            message=message,
            timestamp=datetime.now().isoformat()
        )

        self.alerts.append(alert)
        logger.warning(f"🚨 质量告警 [{severity.upper()}]: {message}")

    def get_current_metrics(self) -> Optional[QualityMetrics]:
        """获取最新的质量指标"""
        return self.metrics_history[-1] if self.metrics_history else None

    def get_metrics_history(self, hours: int = 24) -> List[QualityMetrics]:
        """获取指定时间范围内的历史指标"""
        cutoff_time = datetime.now() - timedelta(hours=hours)

        return [
            metrics for metrics in self.metrics_history
            if datetime.fromisoformat(metrics.timestamp) > cutoff_time
        ]

    def get_active_alerts(self) -> List[QualityAlert]:
        """获取未解决的告警"""
        return [alert for alert in self.alerts if not alert.resolved]

    def resolve_alert(self, alert_id: str, resolution_note: str = ""):
        """解决告警"""
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                alert.resolution_note = resolution_note
                logger.info(f"✅ 告警已解决: {alert_id}")
                return True
        return False

    def analyze_trends(self, period: str = 'day') -> List[QualityTrend]:
        """
        分析质量趋势

        Args:
            period: 分析周期 ('hour', 'day', 'week')

        Returns:
            趋势分析结果列表
        """
        try:
            if not self.metrics_history:
                return []

            # 根据周期筛选数据
            cutoff_hours = {'hour': 1, 'day': 24, 'week': 168}[period]
            recent_metrics = self.get_metrics_history(hours=cutoff_hours)

            if len(recent_metrics) < 2:
                return []

            trends = []
            metric_names = [
                'extraction_accuracy', 'validation_pass_rate', 'confidence_score',
                'knowledge_consistency', 'relationship_correctness', 'user_satisfaction',
                'response_time', 'error_rate', 'hallucination_risk'
            ]

            for metric_name in metric_names:
                # 获取该指标的历史数据
                values = []
                for metrics in recent_metrics:
                    value = getattr(metrics, metric_name, 0)
                    values.append(value)

                if not values:
                    continue

                current_value = values[-1]
                previous_value = values[0]

                # 计算变化率
                if previous_value > 0:
                    change_rate = (current_value - previous_value) / previous_value
                else:
                    change_rate = 0

                # 判断趋势
                if abs(change_rate) < 0.05:
                    trend = 'stable'
                elif (metric_name in ['response_time', 'error_rate', 'hallucination_risk'] and
                      change_rate < 0) or \
                     (metric_name not in ['response_time', 'error_rate', 'hallucination_risk'] and
                      change_rate > 0):
                    trend = 'improving'
                else:
                    trend = 'declining'

                quality_trend = QualityTrend(
                    metric_name=metric_name,
                    period=period,
                    trend=trend,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_rate=change_rate,
                    data_points=values
                )

                trends.append(quality_trend)

            logger.info(f"📈 趋势分析完成: 分析了 {len(trends)} 个指标")
            return trends

        except Exception as e:
            logger.error(f"趋势分析失败: {e}")
            return []

    def get_quality_report(self) -> Dict[str, Any]:
        """获取质量报告"""
        try:
            current_metrics = self.get_current_metrics()
            active_alerts = self.get_active_alerts()
            trends = self.analyze_trends(period='day')

            return {
                'timestamp': datetime.now().isoformat(),
                'current_metrics': asdict(current_metrics) if current_metrics else None,
                'active_alerts_count': len(active_alerts),
                'active_alerts': [asdict(alert) for alert in active_alerts[:10]],  # 最多返回10个
                'trends': [asdict(trend) for trend in trends],
                'health_status': self._get_health_status(current_metrics, active_alerts),
                'total_metrics_collected': len(self.metrics_history)
            }

        except Exception as e:
            logger.error(f"生成质量报告失败: {e}")
            return {'error': str(e)}

    def _get_health_status(self, current_metrics: Optional[QualityMetrics],
                          active_alerts: List[QualityAlert]) -> str:
        """获取系统健康状态"""
        if not current_metrics:
            return 'unknown'

        critical_alerts = [a for a in active_alerts if a.severity == 'critical']
        warning_alerts = [a for a in active_alerts if a.severity == 'warning']

        if critical_alerts:
            return 'critical'
        elif warning_alerts:
            return 'warning'
        else:
            return 'healthy'

    def update_thresholds(self, new_thresholds: Dict[str, float]):
        """更新质量阈值"""
        self.thresholds.update(new_thresholds)
        logger.info(f"📊 质量阈值已更新: {new_thresholds}")

    def export_metrics(self, filepath: str):
        """导出质量指标到文件"""
        try:
            export_data = {
                'export_time': datetime.now().isoformat(),
                'thresholds': self.thresholds,
                'metrics_history': [asdict(m) for m in self.metrics_history],
                'alerts': [asdict(a) for a in self.alerts]
            }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            logger.info(f"✅ 质量指标已导出到: {filepath}")
            return True

        except Exception as e:
            logger.error(f"导出质量指标失败: {e}")
            return False

def main():
    """测试主函数"""
    try:
        # 初始化监控器
        monitor = QualityMonitor(
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_auth=(
                os.getenv("NEO4J_USER", "neo4j"),
                os.getenv("NEO4J_PASSWORD", "aixi1314")
            )
        )

        # 模拟一些数据
        sample_extraction = {
            'keywords': [
                {'name': 'TCP', 'type': 'Protocol', 'confidence': 0.9},
                {'name': 'UDP', 'type': 'Protocol', 'confidence': 0.85}
            ],
            'relationships': [
                {'type': 'DEPENDS_ON', 'confidence': 0.8}
            ]
        }

        sample_validation = {
            'total_validations': 5,
            'validity_rate': 0.8,
            'average_confidence': 0.85
        }

        sample_performance = {
            'response_time': 2.5,
            'error_rate': 0.02
        }

        # 收集指标
        metrics = monitor.collect_metrics(
            sample_extraction,
            sample_validation,
            sample_performance
        )

        print("\n=== 当前质量指标 ===")
        print(f"提取准确率: {metrics.extraction_accuracy:.2f}")
        print(f"验证通过率: {metrics.validation_pass_rate:.2f}")
        print(f"平均置信度: {metrics.confidence_score:.2f}")
        print(f"知识一致性: {metrics.knowledge_consistency:.2f}")
        print(f"关系正确性: {metrics.relationship_correctness:.2f}")
        print(f"用户满意度: {metrics.user_satisfaction:.2f}")
        print(f"响应时间: {metrics.response_time:.2f}s")
        print(f"错误率: {metrics.error_rate:.2f}")
        print(f"幻觉风险: {metrics.hallucination_risk:.2f}")

        # 获取质量报告
        report = monitor.get_quality_report()
        print(f"\n=== 系统健康状态: {report['health_status'].upper()} ===")
        print(f"活跃告警数: {report['active_alerts_count']}")

        # 导出数据
        monitor.export_metrics('quality_metrics_export.json')

        monitor.close()

    except Exception as e:
        logger.error(f"测试失败: {e}")

if __name__ == "__main__":
    main()
