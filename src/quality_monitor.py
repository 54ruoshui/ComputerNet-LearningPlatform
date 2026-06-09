"""
质量监控系统 - 实时监控和评估知识图谱质量
包括多维度的质量指标、异常检测和趋势分析
"""

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import statistics

import structlog
from neo4j import GraphDatabase

from src.exceptions import ConnectionError_
from src.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class QualityMetrics:
    """质量指标数据类"""
    timestamp: str
    extraction_accuracy: float
    validation_pass_rate: float
    confidence_score: float
    knowledge_consistency: float
    relationship_correctness: float
    user_satisfaction: float
    response_time: float
    error_rate: float
    hallucination_risk: float


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
        self.neo4j_uri = neo4j_uri
        self.neo4j_auth = neo4j_auth
        self.driver = None

        self.metrics_history: List[QualityMetrics] = []
        self.max_history_size = 1000

        self.alerts: List[QualityAlert] = []
        self.alert_id_counter = 0

        self.thresholds = {
            'extraction_accuracy': 0.85,
            'validation_pass_rate': 0.80,
            'confidence_score': 0.75,
            'knowledge_consistency': 0.90,
            'relationship_correctness': 0.85,
            'user_satisfaction': 0.80,
            'response_time': 5.0,
            'error_rate': 0.05,
            'hallucination_risk': 0.30
        }

        self._connect()

    def _connect(self):
        try:
            self.driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=self.neo4j_auth
            )
            logger.info("质量监控系统连接Neo4j成功")
        except Exception as e:
            raise ConnectionError_(
                "质量监控系统连接Neo4j失败",
                detail=f"uri={self.neo4j_uri}",
                cause=e,
            ) from e

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("质量监控系统数据库连接已关闭")

    def collect_metrics(self, extraction_results: Dict[str, Any],
                        validation_results: Dict[str, Any],
                        performance_data: Dict[str, Any] = None) -> QualityMetrics:
        try:
            extraction_accuracy = self._calculate_extraction_accuracy(extraction_results)
            validation_pass_rate = self._calculate_validation_pass_rate(validation_results)
            confidence_score = self._calculate_average_confidence(extraction_results, validation_results)
            knowledge_consistency = self._calculate_knowledge_consistency()
            relationship_correctness = self._calculate_relationship_correctness(extraction_results)
            user_satisfaction = self._get_user_satisfaction()
            response_time = performance_data.get('response_time', 0) if performance_data else 0
            error_rate = performance_data.get('error_rate', 0) if performance_data else 0
            hallucination_risk = self._calculate_hallucination_risk(validation_results)

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

            self.metrics_history.append(metrics)
            if len(self.metrics_history) > self.max_history_size:
                self.metrics_history.pop(0)

            self._check_thresholds(metrics)

            logger.info(
                "质量指标收集完成",
                accuracy=extraction_accuracy,
                pass_rate=validation_pass_rate,
                confidence=confidence_score,
            )

            return metrics

        except Exception as e:
            logger.error("收集质量指标失败", error=str(e))
            raise

    def _calculate_extraction_accuracy(self, extraction_results: Dict[str, Any]) -> float:
        try:
            keywords = extraction_results.get('keywords', [])
            relationships = extraction_results.get('relationships', [])
            if not keywords and not relationships:
                return 0.5
            keyword_scores = [kw.get('confidence', 0) for kw in keywords]
            relationship_scores = [rel.get('confidence', 0) for rel in relationships]
            all_scores = keyword_scores + relationship_scores
            if not all_scores:
                return 0.5
            return sum(all_scores) / len(all_scores)
        except Exception as e:
            logger.error("计算提取准确率失败", error=str(e))
            return 0.5

    def _calculate_validation_pass_rate(self, validation_results: Dict[str, Any]) -> float:
        try:
            total_validations = validation_results.get('total_validations', 0)
            validity_rate = validation_results.get('validity_rate', 0.5)
            return validity_rate if total_validations > 0 else 0.5
        except Exception as e:
            logger.error("计算验证通过率失败", error=str(e))
            return 0.5

    def _calculate_average_confidence(self, extraction_results: Dict[str, Any],
                                       validation_results: Dict[str, Any]) -> float:
        try:
            avg_validation_confidence = validation_results.get('average_confidence', 0.5)
            keywords = extraction_results.get('keywords', [])
            relationships = extraction_results.get('relationships', [])
            keyword_scores = [kw.get('confidence', 0) for kw in keywords]
            relationship_scores = [rel.get('confidence', 0) for rel in relationships]
            all_scores = keyword_scores + relationship_scores
            if not all_scores:
                return avg_validation_confidence
            avg_extraction_confidence = sum(all_scores) / len(all_scores)
            return (avg_extraction_confidence + avg_validation_confidence) / 2
        except Exception as e:
            logger.error("计算平均置信度失败", error=str(e))
            return 0.5

    def _calculate_knowledge_consistency(self) -> float:
        try:
            with self.driver.session() as session:
                consistency_check = session.run("""
                    MATCH (a)-[r]->(a)
                    WHERE a.name IS NOT NULL
                    WITH count(r) as self_ref_count
                    MATCH (a)-[r1]->(b)
                    MATCH (a)-[r2]->(b)
                    WHERE r1.type <> r2.type
                    WITH self_ref_count, count(r1) as contradictory_count
                    MATCH ()-[r]->()
                    WITH self_ref_count, contradictory_count, count(r) as total_relationships
                    RETURN CASE
                        WHEN total_relationships = 0 THEN 1.0
                        ELSE 1.0 - ((self_ref_count + contradictory_count) * 2.0 / total_relationships)
                    END as consistency_score
                """)
                result = consistency_check.single()
                if result:
                    score = result['consistency_score']
                    return max(0.0, min(1.0, score))
                return 0.8
        except Exception as e:
            logger.error("计算知识一致性失败", error=str(e))
            return 0.8

    def _calculate_relationship_correctness(self, extraction_results: Dict[str, Any]) -> float:
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
            logger.error("计算关系正确性失败", error=str(e))
            return 0.8

    def _get_user_satisfaction(self) -> float:
        try:
            with self.driver.session() as session:
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
            logger.error("获取用户满意度失败", error=str(e))
            return 0.8

    def _calculate_hallucination_risk(self, validation_results: Dict[str, Any]) -> float:
        try:
            validation_entries = validation_results.get('results', [])
            if not validation_entries:
                return 0.3
            hallucination_scores = []
            for entry in validation_entries:
                result = entry.get('result', {})
                if hasattr(result, 'hallucination_risk'):
                    hallucination_scores.append(result.hallucination_risk)
                elif isinstance(result, dict) and 'hallucination_risk' in result:
                    hallucination_scores.append(result['hallucination_risk'])
            if hallucination_scores:
                return sum(hallucination_scores) / len(hallucination_scores)
            return 0.3
        except Exception as e:
            logger.error("计算幻觉风险失败", error=str(e))
            return 0.3

    def _check_thresholds(self, metrics: QualityMetrics):
        metric_dict = asdict(metrics)
        for metric_name, threshold in self.thresholds.items():
            current_value = metric_dict.get(metric_name, 0)
            should_alert = False
            if metric_name in ['response_time', 'error_rate', 'hallucination_risk']:
                should_alert = current_value > threshold
            else:
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
        deviation_ratio = abs(current_value - threshold) / threshold
        if deviation_ratio > 0.3:
            return 'critical'
        elif deviation_ratio > 0.15:
            return 'warning'
        else:
            return 'info'

    def _create_alert(self, metric_name: str, current_value: float,
                      threshold: float, severity: str):
        self.alert_id_counter += 1
        alert_id = f"ALT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self.alert_id_counter}"
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
        logger.warning("质量告警", severity=severity.upper(), message=message)

    def get_current_metrics(self) -> Optional[QualityMetrics]:
        return self.metrics_history[-1] if self.metrics_history else None

    def get_metrics_history(self, hours: int = 24) -> List[QualityMetrics]:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        return [
            metrics for metrics in self.metrics_history
            if datetime.fromisoformat(metrics.timestamp) > cutoff_time
        ]

    def get_active_alerts(self) -> List[QualityAlert]:
        return [alert for alert in self.alerts if not alert.resolved]

    def resolve_alert(self, alert_id: str, resolution_note: str = ""):
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                alert.resolution_note = resolution_note
                logger.info("告警已解决", alert_id=alert_id)
                return True
        return False

    def analyze_trends(self, period: str = 'day') -> List[QualityTrend]:
        try:
            if not self.metrics_history:
                return []
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
                values = [getattr(metrics, metric_name, 0) for metrics in recent_metrics]
                if not values:
                    continue
                current_value = values[-1]
                previous_value = values[0]
                if previous_value > 0:
                    change_rate = (current_value - previous_value) / previous_value
                else:
                    change_rate = 0

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

            logger.info("趋势分析完成", metrics_count=len(trends))
            return trends

        except Exception as e:
            logger.error("趋势分析失败", error=str(e))
            return []

    def get_quality_report(self) -> Dict[str, Any]:
        try:
            current_metrics = self.get_current_metrics()
            active_alerts = self.get_active_alerts()
            trends = self.analyze_trends(period='day')
            return {
                'timestamp': datetime.now().isoformat(),
                'current_metrics': asdict(current_metrics) if current_metrics else None,
                'active_alerts_count': len(active_alerts),
                'active_alerts': [asdict(alert) for alert in active_alerts[:10]],
                'trends': [asdict(trend) for trend in trends],
                'health_status': self._get_health_status(current_metrics, active_alerts),
                'total_metrics_collected': len(self.metrics_history)
            }
        except Exception as e:
            logger.error("生成质量报告失败", error=str(e))
            return {'error': str(e)}

    def _get_health_status(self, current_metrics: Optional[QualityMetrics],
                           active_alerts: List[QualityAlert]) -> str:
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
        self.thresholds.update(new_thresholds)
        logger.info("质量阈值已更新", thresholds=new_thresholds)

    def export_metrics(self, filepath: str):
        try:
            export_data = {
                'export_time': datetime.now().isoformat(),
                'thresholds': self.thresholds,
                'metrics_history': [asdict(m) for m in self.metrics_history],
                'alerts': [asdict(a) for a in self.alerts]
            }
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            logger.info("质量指标已导出", filepath=filepath)
            return True
        except Exception as e:
            logger.error("导出质量指标失败", error=str(e))
            return False
