"""
GraphRAG社区层次索引构建脚本
仿照Microsoft GraphRAG架构，构建社区层次和生成社区摘要

核心功能：
1. 读取PDF文档并分块
2. 使用LLM提取实体和关系
3. 使用Leiden算法进行层次化社区检测
4. 为每个社区生成LLM摘要报告
5. 将所有数据写入Neo4j图数据库

使用方法：
    python scripts/build_index_from_docs.py

依赖：
    pip install neo4j zhipuai python-dotenv pypdf igraph networkx

参考：
    https://github.com/microsoft/graphrag
"""

import os
import sys
import re
import json
import time
import logging
import hashlib
import pickle
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase
from zhipuai import ZhipuAI
from dotenv import load_dotenv

# 尝试导入阿里云DashScope (Qwen) 用于向量嵌入
try:
    import dashscope
    from dashscope import TextEmbedding
    HAS_DASHSCOPE = True
except ImportError:
    HAS_DASHSCOPE = False

# 尝试导入社区检测库
try:
    import igraph as ig
    HAS_IGRAPH = True
except ImportError:
    HAS_IGRAPH = False
    import networkx as nx
    from networkx.algorithms import community

# 尝试导入PDF解析库
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()


# ==================== 配置类 ====================

@dataclass
class GraphRAGIndexerConfig:
    """GraphRAG索引器配置"""
    # Neo4j配置
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    # 智谱AI配置
    zhipu_api_key: str = os.getenv("ZHIPUAI_API_KEY", "")
    zhipu_model: str = os.getenv("ZHIPUAI_MODEL", "glm-4-flash")

    # 文本分块配置
    chunk_size: int = 1200  # 每个文本块的最大token数
    chunk_overlap: int = 100  # 文本块重叠大小

    # 实体提取配置
    max_entities_per_chunk: int = 30  # 每个块最多提取的实体数

    # 社区检测配置
    min_community_size: int = 3  # 最小社区大小
    max_community_levels: int = 3  # 最大社区层级数

    # 社区报告配置
    max_report_tokens: int = 1500  # 社区报告最大token数

    # Embedding配置（用于向量检索）
    qwen_api_key: str = os.getenv("QWEN_API_KEY", "")
    qwen_embedding_model: str = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v3")
    embedding_dimension: int = 1024  # Qwen text-embedding-v3 默认1024维
    embedding_batch_size: int = 20  # 每批处理数量
    embedding_retry_times: int = 3
    embedding_retry_delay: float = 1.0

    # 断点续传配置
    checkpoint_dir: str = str(Path(__file__).parent.parent / "data" / "checkpoints")
    checkpoint_interval: int = 5  # 每处理多少个chunk保存一次检查点


@dataclass
class Checkpoint:
    """检查点数据结构"""
    pdf_path: str
    current_step: str  # 当前步骤: parsing, chunking, extracting, detecting, reporting, writing
    chunk_index: int = 0  # 当前处理的chunk索引
    total_chunks: int = 0  # 总chunk数
    entities: List[Dict] = field(default_factory=list)  # 已提取的实体
    relationships: List[Dict] = field(default_factory=list)  # 已提取的关系
    communities: List[Dict] = field(default_factory=list)  # 已生成的社区
    chunks_data: List[Dict] = field(default_factory=list)  # 文本块数据（用于恢复）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class CheckpointManager:
    """检查点管理器 - 负责保存和恢复进度"""

    def __init__(self, checkpoint_dir: str):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.checkpoint_dir / "build_index_checkpoint.pkl"

    def save(self, checkpoint: Checkpoint):
        """保存检查点"""
        try:
            with open(self.checkpoint_file, 'wb') as f:
                pickle.dump(checkpoint, f)
            logger.info(f"💾 检查点已保存: 步骤={checkpoint.current_step}, chunk={checkpoint.chunk_index}/{checkpoint.total_chunks}")
        except Exception as e:
            logger.error(f"保存检查点失败: {e}")

    def load(self, pdf_path: str = None) -> Optional[Checkpoint]:
        """加载检查点"""
        try:
            if self.checkpoint_file.exists():
                with open(self.checkpoint_file, 'rb') as f:
                    checkpoint = pickle.load(f)

                # 如果指定了PDF路径，检查是否匹配
                if pdf_path and checkpoint.pdf_path != pdf_path:
                    logger.info(f"检查点的PDF文件不匹配，将重新开始")
                    return None

                logger.info(f"📂 发现检查点: {checkpoint.timestamp}")
                logger.info(f"   步骤: {checkpoint.current_step}")
                logger.info(f"   进度: {checkpoint.chunk_index}/{checkpoint.total_chunks} chunks")
                logger.info(f"   实体: {len(checkpoint.entities)}, 关系: {len(checkpoint.relationships)}")

                return checkpoint
        except Exception as e:
            logger.warning(f"加载检查点失败: {e}")
        return None

    def clear(self):
        """清除检查点"""
        try:
            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
                logger.info("🗑️ 检查点已清除")
        except Exception as e:
            logger.warning(f"清除检查点失败: {e}")

    def exists(self) -> bool:
        """检查是否存在检查点"""
        return self.checkpoint_file.exists()


@dataclass
class Entity:
    """实体数据结构"""
    id: str
    name: str
    entity_type: str
    description: str = ""
    source_chunks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "description": self.description,
            "source_chunks": self.source_chunks
        }


@dataclass
class Relationship:
    """关系数据结构"""
    id: str
    source_id: str
    target_id: str
    relationship_type: str
    description: str = ""
    weight: float = 1.0
    source_chunks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "description": self.description,
            "weight": self.weight,
            "source_chunks": self.source_chunks
        }


@dataclass
class Community:
    """社区数据结构"""
    id: str
    title: str
    level: int
    summary: str = ""
    full_content: str = ""
    rank: float = 0.0
    rank_explanation: str = ""
    findings: List[Dict] = field(default_factory=list)
    entity_ids: List[str] = field(default_factory=list)
    relationship_ids: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "summary": self.summary,
            "full_content": self.full_content,
            "rank": self.rank,
            "rank_explanation": self.rank_explanation,
            "findings": self.findings,
            "entity_ids": self.entity_ids,
            "relationship_ids": self.relationship_ids,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids
        }


@dataclass
class TextChunk:
    """文本块数据结构"""
    id: str
    content: str
    source: str
    page_number: int = 0
    order: int = 0


# ==================== PDF解析器 ====================

class PDFParser:
    """PDF文档解析器"""

    def __init__(self):
        if not HAS_FITZ and not HAS_PDFPLUMBER and not HAS_PYPDF:
            raise ImportError("请安装PyMuPDF库: pip install PyMuPDF")

    def parse(self, pdf_path: str) -> List[TextChunk]:
        """解析PDF文档，返回文本块列表"""
        chunks = []

        # 优先使用PyMuPDF（最健壮，不易崩溃）
        if HAS_FITZ:
            try:
                logger.info("   尝试使用 PyMuPDF 解析...")
                chunks = self._parse_with_fitz(pdf_path)
                if chunks:
                    logger.info(f"   PyMuPDF 解析成功，提取到 {len(chunks)} 个文本块")
                    return chunks
            except Exception as e:
                logger.warning(f"   PyMuPDF 解析失败: {e}")

        # 备用pdfplumber
        if HAS_PDFPLUMBER:
            try:
                logger.info("   尝试使用 pdfplumber 解析...")
                chunks = self._parse_with_pdfplumber(pdf_path)
                if chunks:
                    logger.info(f"   pdfplumber 解析成功，提取到 {len(chunks)} 个文本块")
                    return chunks
            except Exception as e:
                logger.warning(f"   pdfplumber 解析失败: {e}")

        # 备用pypdf
        if HAS_PYPDF:
            try:
                logger.info("   尝试使用 pypdf 解析...")
                chunks = self._parse_with_pypdf(pdf_path)
                if chunks:
                    logger.info(f"   pypdf 解析成功，提取到 {len(chunks)} 个文本块")
                    return chunks
            except Exception as e:
                logger.warning(f"   pypdf 解析失败: {e}")

        if not chunks:
            raise ValueError(f"无法解析PDF文件: {pdf_path}，请检查文件是否损坏")

        return chunks

    def _parse_with_fitz(self, pdf_path: str) -> List[TextChunk]:
        """使用PyMuPDF解析PDF"""
        chunks = []
        doc = fitz.open(pdf_path)
        try:
            num_pages = doc.page_count
            logger.info(f"   PDF共有 {num_pages} 页")
            for page_num in range(num_pages):
                if page_num % 50 == 0:
                    logger.info(f"   正在解析第 {page_num + 1}/{num_pages} 页...")
                try:
                    page = doc[page_num]
                    text = page.get_text()
                    if text and text.strip():
                        paragraphs = self._split_into_paragraphs(text)
                        for order, para in enumerate(paragraphs):
                            chunk_id = self._generate_chunk_id(para, page_num, order)
                            chunks.append(TextChunk(
                                id=chunk_id,
                                content=para,
                                source=pdf_path,
                                page_number=page_num + 1,
                                order=order
                            ))
                except Exception as e:
                    logger.warning(f"   第 {page_num + 1} 页解析失败: {e}，跳过该页")
                    continue
        finally:
            doc.close()
        return chunks

    def _parse_with_pdfplumber(self, pdf_path: str) -> List[TextChunk]:
        """使用pdfplumber解析PDF"""
        chunks = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                logger.info(f"   PDF共有 {total_pages} 页")

                for page_num, page in enumerate(pdf.pages):
                    if page_num % 50 == 0:
                        logger.info(f"   正在解析第 {page_num + 1}/{total_pages} 页...")

                    try:
                        text = page.extract_text()
                        if text and text.strip():
                            # 按段落分割
                            paragraphs = self._split_into_paragraphs(text)
                            for order, para in enumerate(paragraphs):
                                chunk_id = self._generate_chunk_id(para, page_num, order)
                                chunks.append(TextChunk(
                                    id=chunk_id,
                                    content=para,
                                    source=pdf_path,
                                    page_number=page_num + 1,
                                    order=order
                                ))
                    except Exception as e:
                        logger.warning(f"   第 {page_num + 1} 页解析失败: {e}，跳过该页")
                        continue

        except Exception as e:
            logger.error(f"   pdfplumber 打开文件失败: {e}")
            raise

        return chunks

    def _parse_with_pypdf(self, pdf_path: str) -> List[TextChunk]:
        """使用pypdf解析PDF"""
        chunks = []

        try:
            with open(pdf_path, 'rb') as f:
                reader = pypdf.PdfReader(f)

                # 尝试获取页数，处理损坏的PDF
                try:
                    num_pages = len(reader.pages)
                    logger.info(f"   PDF共有 {num_pages} 页")
                except Exception as e:
                    logger.error(f"   无法读取PDF页数: {e}")
                    raise ValueError(f"PDF文件可能已损坏: {pdf_path}")

                for page_num in range(num_pages):
                    if page_num % 50 == 0:
                        logger.info(f"   正在解析第 {page_num + 1}/{num_pages} 页...")
                    try:
                        page = reader.pages[page_num]
                        text = page.extract_text()
                        if text and text.strip():
                            paragraphs = self._split_into_paragraphs(text)
                            for order, para in enumerate(paragraphs):
                                chunk_id = self._generate_chunk_id(para, page_num, order)
                                chunks.append(TextChunk(
                                    id=chunk_id,
                                    content=para,
                                    source=pdf_path,
                                    page_number=page_num + 1,
                                    order=order
                                ))
                    except Exception as e:
                        logger.warning(f"   第 {page_num + 1} 页解析失败: {e}，跳过该页")
                        continue

        except pypdf.errors.PdfReadError as e:
            raise ValueError(f"PDF文件损坏或格式不支持: {e}")
        except Exception as e:
            logger.error(f"   pypdf 解析过程中发生未预期错误: {e}")
            raise

        return chunks

    def _split_into_paragraphs(self, text: str) -> List[str]:
        """将文本分割成段落"""
        # 按连续的换行符分割
        paragraphs = re.split(r'\n\s*\n', text)
        # 过滤空段落并清理
        return [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 20]

    def _generate_chunk_id(self, content: str, page_num: int, order: int) -> str:
        """生成文本块ID"""
        combined = f"{content[:50]}_{page_num}_{order}"
        return f"chunk_{hashlib.md5(combined.encode()).hexdigest()[:12]}"


# ==================== 文本分块器 ====================

class TextChunker:
    """文本分块器 - 将文档分割成合适大小的块"""

    def __init__(self, chunk_size: int = 1200, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text_chunks: List[TextChunk], config: GraphRAGIndexerConfig) -> List[TextChunk]:
        """
        将文本块重新分割成合适大小的块

        参考GraphRAG的chunking策略：
        - 按句子边界分割
        - 保持语义完整性
        - 添加重叠以保持上下文
        """
        result = []
        chunk_counter = 0

        for tc in text_chunks:
            if len(tc.content) <= self.chunk_size:
                # 如果内容已经在限制内，直接使用
                result.append(tc)
            else:
                # 需要分割
                sub_chunks = self._split_large_content(tc.content)
                for i, sub_content in enumerate(sub_chunks):
                    chunk_id = f"{tc.id}_sub_{i}"
                    result.append(TextChunk(
                        id=chunk_id,
                        content=sub_content,
                        source=tc.source,
                        page_number=tc.page_number,
                        order=chunk_counter
                    ))
                    chunk_counter += 1

        return result

    def _split_large_content(self, content: str) -> List[str]:
        """分割大段内容"""
        # 按句子分割
        sentences = re.split(r'([。！？.!?]\s*)', content)

        # 重新组合句子
        combined_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
            if sentence.strip():
                combined_sentences.append(sentence.strip())

        if len(sentences) % 2 == 1 and sentences[-1].strip():
            combined_sentences.append(sentences[-1].strip())

        # 按chunk_size组合
        chunks = []
        current_chunk = ""

        for sentence in combined_sentences:
            if len(current_chunk) + len(sentence) <= self.chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks


# ==================== 实体关系提取器 ====================

class EntityRelationshipExtractor:
    """
    实体和关系提取器

    使用LLM从文本块中提取实体和关系
    参考GraphRAG的entity extraction策略
    """

    # 实体类型定义（计算机网络领域）- 与 enhanced_prompt_engineering.py 保持一致
    ENTITY_TYPES = [
        "Protocol",      # 协议：TCP、UDP、HTTP、HTTPS、DNS、DHCP等
        "Device",        # 设备：路由器、交换机、防火墙、网关等
        "Layer",         # 层次：应用层、传输层、网络层、数据链路层、物理层
        "Knowledge",     # 知识：三次握手、拥塞控制、流量控制、VLAN等
        "SecurityConcept",  # 安全概念：加密、认证、防火墙、VPN等
        "NetworkType",   # 网络类型：局域网、广域网、以太网、WiFi等
        "Problem",       # 问题：网络不通、丢包、延迟、环路等
        "Solution"       # 解决方案：网络排查、故障定位、性能优化等
    ]

    # 关系类型定义 - 与 enhanced_prompt_engineering.py 保持一致
    RELATIONSHIP_TYPES = [
        "APPLY_TO",      # 应用于（如：TCP应用于传输层）
        "DEPENDS_ON",    # 依赖于（如：HTTP依赖于TCP）
        "RELATED_TO",    # 相关于（一般相关关系，谨慎使用）
        "WORKS_WITH",    # 协同工作（如：路由器与交换机协同工作）
        "PROTECTS",      # 保护（如：防火墙保护网络）
        "ATTACKS",       # 攻击（如：DDoS攻击服务器）
        "SOLVED_BY",     # 通过...解决（如：网络问题通过路由器解决）
        "BELONGS_TO",    # 属于（如：TCP属于传输层协议）
        "HAS_FUNCTION",  # 具有功能（如：路由器具有路由功能）
        "BETWEEN"        # 介于之间（如：网关介于两个网络之间）
    ]

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config
        self.llm_client = ZhipuAI(api_key=config.zhipu_api_key)

    def extract_from_chunk(self, chunk: TextChunk) -> Tuple[List[Entity], List[Relationship]]:
        """从文本块中提取实体和关系"""
        try:
            response = self.llm_client.chat.completions.create(
                model=self.config.zhipu_model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_extraction_prompt()
                    },
                    {
                        "role": "user",
                        "content": f"请从以下文本中提取实体和关系：\n\n{chunk.content}"
                    }
                ],
                temperature=0.1,
                max_tokens=2000
            )

            result = response.choices[0].message.content
            return self._parse_extraction_result(result, chunk.id)

        except Exception as e:
            logger.error(f"实体提取失败 (chunk: {chunk.id}): {e}")
            return [], []

    def _get_extraction_prompt(self) -> str:
        """获取实体关系提取的提示词"""
        return f"""你是一个计算机网络领域的知识图谱构建专家。请从给定的文本中提取实体和关系。

## 实体类型
请将实体归类为以下类型之一：
{json.dumps(self.ENTITY_TYPES, ensure_ascii=False, indent=2)}

## 关系类型
请使用以下关系类型描述实体间的关系：
{json.dumps(self.RELATIONSHIP_TYPES, ensure_ascii=False, indent=2)}

## 输出格式
请严格按照以下JSON格式输出，不要添加任何其他内容：

```json
{{
    "entities": [
        {{
            "name": "实体名称",
            "type": "实体类型",
            "description": "实体描述（简洁明了）"
        }}
    ],
    "relationships": [
        {{
            "source": "源实体名称",
            "target": "目标实体名称",
            "type": "关系类型",
            "description": "关系描述"
        }}
    ]
}}
```

## 注意事项
1. 只提取文本中明确提到的实体和关系
2. 实体名称应使用标准术语
3. 描述应简洁但信息丰富
4. 如果文本中没有明确的实体或关系，返回空数组
5. 确保输出是有效的JSON格式"""

    def _parse_extraction_result(self, result: str, chunk_id: str) -> Tuple[List[Entity], List[Relationship]]:
        """解析LLM提取结果"""
        entities = []
        relationships = []

        try:
            # 提取JSON部分
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析
                json_str = result

            data = json.loads(json_str)

            # 解析实体
            for e in data.get("entities", []):
                name = e.get("name", "").strip()
                if name:
                    entity_id = self._generate_entity_id(name)
                    entities.append(Entity(
                        id=entity_id,
                        name=name,
                        entity_type=e.get("type", "概念"),
                        description=e.get("description", ""),
                        source_chunks=[chunk_id]
                    ))

            # 解析关系
            for r in data.get("relationships", []):
                source_name = r.get("source", "").strip()
                target_name = r.get("target", "").strip()
                if source_name and target_name:
                    source_id = self._generate_entity_id(source_name)
                    target_id = self._generate_entity_id(target_name)
                    rel_id = self._generate_relationship_id(source_id, target_id, r.get("type", ""))
                    relationships.append(Relationship(
                        id=rel_id,
                        source_id=source_id,
                        target_id=target_id,
                        relationship_type=r.get("type", "关联"),
                        description=r.get("description", ""),
                        source_chunks=[chunk_id]
                    ))

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
        except Exception as e:
            logger.error(f"解析提取结果失败: {e}")

        return entities, relationships

    def _generate_entity_id(self, name: str) -> str:
        """生成实体ID"""
        return f"entity_{hashlib.md5(name.encode()).hexdigest()[:12]}"

    def _generate_relationship_id(self, source_id: str, target_id: str, rel_type: str) -> str:
        """生成关系ID"""
        combined = f"{source_id}_{rel_type}_{target_id}"
        return f"rel_{hashlib.md5(combined.encode()).hexdigest()[:12]}"


# ==================== 社区检测器 ====================

class CommunityDetector:
    """
    社区检测器
    

    使用层次化Leiden算法进行社区检测
    参考GraphRAG的community detection策略
    """

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config
        self.min_community_size = config.min_community_size
        self.max_levels = config.max_community_levels

    def detect_communities(
        self,
        entities: List[Entity],
        relationships: List[Relationship]
    ) -> List[Community]:
        """
        检测社区并构建层次结构

        使用Leiden算法进行层次化聚类
        """
        if not entities or not relationships:
            return []

        # 构建图
        if HAS_IGRAPH:
            return self._detect_with_igraph(entities, relationships)
        else:
            return self._detect_with_networkx(entities, relationships)

    def _detect_with_igraph(
        self,
        entities: List[Entity],
        relationships: List[Relationship]
    ) -> List[Community]:
        """使用igraph进行社区检测（推荐）"""
        # 创建实体名称到索引的映射
        entity_map = {e.id: i for i, e in enumerate(entities)}
        entity_reverse_map = {i: e for i, e in enumerate(entities)}

        # 创建igraph图
        edges = []
        weights = []
        for rel in relationships:
            if rel.source_id in entity_map and rel.target_id in entity_map:
                edges.append((entity_map[rel.source_id], entity_map[rel.target_id]))
                weights.append(rel.weight)

        if not edges:
            return []

        g = ig.Graph(len(entities), edges)
        g.es["weight"] = weights

        # 使用Leiden算法进行层次化社区检测
        communities_list = []

        # 获取不同分辨率级别的社区
        for resolution in [1.0, 0.5, 0.25]:
            try:
                partition = g.community_leiden(
                    weights="weight",
                    resolution_parameter=resolution,
                    n_iterations=2
                )

                level = len(communities_list)
                for comm_idx, member_ids in enumerate(partition):
                    if len(member_ids) >= self.min_community_size:
                        community_entities = [entity_reverse_map[i] for i in member_ids]
                        comm = self._create_community(
                            community_entities,
                            relationships,
                            level,
                            comm_idx
                        )
                        communities_list.append(comm)
            except Exception as e:
                logger.warning(f"Leiden算法执行失败 (resolution={resolution}): {e}")

        # 建立层次关系
        self._build_hierarchy(communities_list, entities)

        return communities_list

    def _detect_with_networkx(
        self,
        entities: List[Entity],
        relationships: List[Relationship]
    ) -> List[Community]:
        """使用networkx进行社区检测（备用方案）"""
        # 创建实体ID到索引的映射
        entity_map = {e.id: i for i, e in enumerate(entities)}

        # 创建networkx图
        G = nx.Graph()

        # 添加节点
        for entity in entities:
            G.add_node(entity.id, entity=entity)

        # 添加边
        for rel in relationships:
            if rel.source_id in G.nodes and rel.target_id in G.nodes:
                G.add_edge(rel.source_id, rel.target_id,
                          weight=rel.weight, relationship=rel)

        if G.number_of_edges() == 0:
            return []

        communities_list = []

        # 使用贪婪模块度社区检测
        try:
            communities_generator = community.greedy_modularity_communities(G)

            for comm_idx, comm_nodes in enumerate(communities_generator):
                if len(comm_nodes) >= self.min_community_size:
                    community_entities = [entities[entity_map[eid]] if eid in entity_map else None
                                         for eid in comm_nodes]
                    community_entities = [e for e in community_entities if e is not None]

                    if community_entities:
                        comm = self._create_community(
                            community_entities,
                            relationships,
                            0,  # 单层
                            comm_idx
                        )
                        communities_list.append(comm)
        except Exception as e:
            logger.error(f"NetworkX社区检测失败: {e}")

        return communities_list

    def _create_community(
        self,
        entities: List[Entity],
        relationships: List[Relationship],
        level: int,
        index: int
    ) -> Community:
        """创建社区对象"""
        entity_ids = [e.id for e in entities]
        entity_names = [e.name for e in entities[:5]]  # 取前5个作为代表

        # 找出社区内的关系
        entity_set = set(entity_ids)
        rel_ids = []
        for rel in relationships:
            if rel.source_id in entity_set and rel.target_id in entity_set:
                rel_ids.append(rel.id)

        # 生成社区标题
        if entity_names:
            title = f"社区: {', '.join(entity_names[:3])}"
            if len(entity_names) > 3:
                title += f" 等{len(entity_names)}个实体"
        else:
            title = f"社区_{level}_{index}"

        return Community(
            id=f"community_{level}_{index}",
            title=title,
            level=level,
            entity_ids=entity_ids,
            relationship_ids=rel_ids
        )

    def _build_hierarchy(self, communities: List[Community], entities: List[Entity]):
        """建立社区层次关系"""
        # 按层级排序
        communities.sort(key=lambda c: c.level)

        # 对于每个高层社区，找到其包含的低层社区
        for i, high_comm in enumerate(communities):
            if high_comm.level > 0:
                # 查找可能的子社区
                for low_comm in communities:
                    if low_comm.level < high_comm.level:
                        # 检查是否有实体重叠
                        overlap = set(high_comm.entity_ids) & set(low_comm.entity_ids)
                        if overlap and len(overlap) >= self.min_community_size:
                            # 建立父子关系
                            low_comm.parent_id = high_comm.id
                            if low_comm.id not in high_comm.children_ids:
                                high_comm.children_ids.append(low_comm.id)


# ==================== 社区报告生成器 ====================

class CommunityReportGenerator:
    """
    社区报告生成器

    使用LLM为每个社区生成结构化摘要报告
    参考GraphRAG的community report generation策略
    """

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config
        self.llm_client = ZhipuAI(api_key=config.zhipu_api_key)

    # 失败的社区ID列表（用于记录）
    _failed_communities: List[str] = []

    def generate_report(self, community: Community, entities: List[Entity],
                       relationships: List[Relationship], max_retries: int = 3) -> Tuple[Community, bool]:
        """
        为社区生成报告

        参数:
            community: 社区对象
            entities: 实体列表
            relationships: 关系列表
            max_retries: 最大重试次数

        返回:
            Tuple[Community, bool]: (更新后的社区, 是否成功)
        """
        import time
        last_error = None

        for attempt in range(max_retries):
            try:
                # 准备社区上下文
                context = self._build_community_context(community, entities, relationships)

                # 调用LLM生成报告
                response = self.llm_client.chat.completions.create(
                    model=self.config.zhipu_model,
                    messages=[
                        {
                            "role": "system",
                            "content": self._get_report_generation_prompt()
                        },
                        {
                            "role": "user",
                            "content": f"请为以下社区生成报告：\n\n{context}"
                        }
                    ],
                    temperature=0.3,
                    max_tokens=self.config.max_report_tokens
                )

                result = response.choices[0].message.content
                parsed_community = self._parse_report_result(community, result)

                if attempt > 0:
                    logger.info(f"✅ 社区报告重试成功 (community: {community.id}, 第{attempt+1}次尝试)")

                return parsed_community, True

            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ 社区报告生成失败 (community: {community.id}, 尝试 {attempt + 1}/{max_retries}): {e}")

                # 如果不是最后一次尝试，等待后重试
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)  # 5s, 10s, 15s
                    logger.info(f"   等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)

        # 所有重试都失败，记录失败社区
        logger.error(f"❌ 社区报告最终失败 (community: {community.id}): {last_error}")
        CommunityReportGenerator._failed_communities.append(community.id)

        # 返回基本报告
        community.summary = f"包含{len(community.entity_ids)}个实体的社区"
        community.full_content = community.summary
        return community, False

    @classmethod
    def get_failed_communities(cls) -> List[str]:
        """获取失败的社区ID列表"""
        return cls._failed_communities

    @classmethod
    def save_failed_communities_to_file(cls, filepath: str = "failed_communities.txt"):
        """将失败的社区ID保存到文件"""
        if cls._failed_communities:
            with open(filepath, 'w', encoding='utf-8') as f:
                for comm_id in cls._failed_communities:
                    f.write(f"{comm_id}\n")
            logger.info(f"📝 已保存 {len(cls._failed_communities)} 个失败社区ID到 {filepath}")

    def _build_community_context(
        self,
        community: Community,
        entities: List[Entity],
        relationships: List[Relationship]
    ) -> str:
        """构建社区上下文信息"""
        entity_map = {e.id: e for e in entities}
        rel_map = {r.id: r for r in relationships}

        # 实体信息
        entity_info = []
        for eid in community.entity_ids[:20]:  # 限制数量
            if eid in entity_map:
                e = entity_map[eid]
                entity_info.append(f"- {e.name} ({e.entity_type}): {e.description}")

        # 关系信息
        rel_info = []
        for rid in community.relationship_ids[:20]:
            if rid in rel_map:
                r = rel_map[rid]
                source = entity_map.get(r.source_id, Entity(id="", name="未知", entity_type=""))
                target = entity_map.get(r.target_id, Entity(id="", name="未知", entity_type=""))
                rel_info.append(f"- {source.name} --[{r.relationship_type}]--> {target.name}")

        context = f"""## 社区信息
- 社区ID: {community.id}
- 层级: {community.level}
- 实体数量: {len(community.entity_ids)}
- 关系数量: {len(community.relationship_ids)}

## 实体列表
{chr(10).join(entity_info)}

## 关系列表
{chr(10).join(rel_info)}
"""
        return context

    def _get_report_generation_prompt(self) -> str:
        """获取社区报告生成的提示词"""
        return """你是一个计算机网络领域的知识图谱专家。请为给定的社区生成一份结构化的摘要报告。

## 报告要求
报告应包含以下部分：

1. **标题**: 简洁描述社区主题（不超过20字）
2. **摘要**: 概述社区的核心内容（100-200字）
3. **重要性评级**: 1-10分，评估社区的重要性
4. **评级说明**: 解释为什么给出这个评级
5. **关键发现**: 列出3-5个关键洞察或发现

## 输出格式
请严格按照以下JSON格式输出：

```json
{
    "title": "社区标题",
    "summary": "社区摘要...",
    "rank": 8,
    "rank_explanation": "评级说明...",
    "findings": [
        {
            "summary": "发现1摘要",
            "explanation": "发现1详细解释"
        }
    ]
}
```

## 注意事项
1. 专注于实体间的关系和模式
2. 识别知识领域内的关键概念
3. 发现潜在的技术趋势或问题
4. 保持报告的客观性和专业性
5. 确保输出是有效的JSON格式"""

    def _parse_report_result(self, community: Community, result: str) -> Community:
        """解析LLM生成的报告"""
        try:
            # 提取JSON部分
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', result)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = result

            data = json.loads(json_str)

            community.title = data.get("title", community.title)
            community.summary = data.get("summary", "")
            community.rank = float(data.get("rank", 0))
            community.rank_explanation = data.get("rank_explanation", "")
            community.findings = data.get("findings", [])

            # 生成完整内容
            community.full_content = self._build_full_content(community)

        except json.JSONDecodeError as e:
            logger.warning(f"报告JSON解析失败: {e}")
            community.summary = result[:500]
            community.full_content = result
        except Exception as e:
            logger.error(f"解析报告结果失败: {e}")

        return community

    def _build_full_content(self, community: Community) -> str:
        """构建完整的社区报告内容"""
        parts = [
            f"# {community.title}",
            "",
            f"## 摘要\n{community.summary}",
            "",
            f"## 重要性评级: {community.rank}/10",
            f"评级说明: {community.rank_explanation}",
            ""
        ]

        if community.findings:
            parts.append("## 关键发现")
            for i, finding in enumerate(community.findings, 1):
                parts.append(f"\n### 发现 {i}")
                parts.append(f"- **摘要**: {finding.get('summary', '')}")
                parts.append(f"- **解释**: {finding.get('explanation', '')}")

        return "\n".join(parts)


# ==================== 向量嵌入生成器 ====================

class EmbeddingGenerator:
    """
    向量嵌入生成器

    使用 Qwen (DashScope) API 生成文本向量嵌入，
    支持批量处理和重试机制。
    """

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config
        self.model_name = config.qwen_embedding_model
        self.dimension = config.embedding_dimension
        self.batch_size = config.embedding_batch_size
        self.retry_times = config.embedding_retry_times
        self.retry_delay = config.embedding_retry_delay

        if HAS_DASHSCOPE and config.qwen_api_key:
            dashscope.api_key = config.qwen_api_key
            self._ready = True
            logger.info(f"✅ Embedding生成器初始化完成，使用 {self.model_name}")
        else:
            self._ready = False
            reason = "DashScope未安装" if not HAS_DASHSCOPE else "QWEN_API_KEY未配置"
            logger.info(f"ℹ️ Embedding生成器不可用（{reason}），将跳过向量生成")

    @property
    def ready(self) -> bool:
        return self._ready

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """生成单个文本的向量嵌入，带重试机制"""
        if not self._ready:
            return None

        text = text.strip()
        if not text:
            return None

        for attempt in range(1, self.retry_times + 1):
            try:
                response = TextEmbedding.call(
                    model=self.model_name,
                    input=text
                )
                if response.status_code == 200:
                    embedding = response.output['embeddings'][0]['embedding']
                    return embedding
                else:
                    logger.warning(f"Embedding API返回非200状态码: {response.status_code}, 尝试 {attempt}/{self.retry_times}")
            except Exception as e:
                logger.warning(f"生成Embedding失败（尝试 {attempt}/{self.retry_times}）: {e}")

            if attempt < self.retry_times:
                time.sleep(self.retry_delay)

        logger.error(f"生成Embedding最终失败，已重试 {self.retry_times} 次")
        return None

    def generate_batch_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量生成向量嵌入"""
        results = []
        for text in texts:
            embedding = self.generate_embedding(text)
            results.append(embedding)
            if embedding:
                logger.debug(f"   生成向量成功: {text[:30]}...")
        return results


# ==================== Neo4j数据写入器 ====================

class Neo4jDataWriter:
    """
    Neo4j数据写入器

    将实体、关系和社区写入Neo4j图数据库
    """

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config
        self.driver = GraphDatabase.driver(
            config.neo4j_uri,
            auth=(config.neo4j_user, config.neo4j_password)
        )

    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()

    def clear_existing_data(self):
        """清除现有数据"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("🗑️ 已清除Neo4j中的所有数据")

    def write_entities(self, entities: List[Entity]):
        """写入实体（使用具体类型标签 + 通用Entity标签）"""
        with self.driver.session() as session:
            for entity in entities:
                # 将entity_type转换为合法的Neo4j标签
                type_label = self._sanitize_label(entity.entity_type)
                # 同时创建通用Entity标签和具体类型标签
                session.run(f"""
                    MERGE (e:Entity:{type_label} {{id: $id}})
                    SET e.name = $name,
                        e.entity_type = $entity_type,
                        e.description = $description,
                        e.source_chunks = $source_chunks
                """, **entity.to_dict())

        logger.info(f"✅ 写入 {len(entities)} 个实体")

    def _sanitize_label(self, label: str) -> str:
        """清理标签名称（Neo4j要求大写字母开头，只含字母数字下划线）"""
        cleaned = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fff]', '_', label.strip())
        # 确保首字符是大写字母或下划线
        if cleaned and cleaned[0].isdigit():
            cleaned = '_' + cleaned
        if cleaned and cleaned[0].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned or 'Unknown'

    def write_relationships(self, relationships: List[Relationship]):
        """写入关系（使用具体关系类型如WORKS_WITH、SOLVED_BY等）"""
        with self.driver.session() as session:
            for rel in relationships:
                # 使用动态关系类型（如 WORKS_WITH, SOLVED_BY, APPLY_TO 等）
                rel_type = self._sanitize_rel_type(rel.relationship_type)
                session.run(f"""
                    MATCH (source:Entity {{id: $source_id}})
                    MATCH (target:Entity {{id: $target_id}})
                    MERGE (source)-[r:{rel_type} {{id: $id}}]->(target)
                    SET r.relationship_type = $relationship_type,
                        r.description = $description,
                        r.weight = $weight,
                        r.source_chunks = $source_chunks
                """, **rel.to_dict())

        logger.info(f"✅ 写入 {len(relationships)} 个关系")

    def write_communities(self, communities: List[Community]):
        """写入社区"""
        with self.driver.session() as session:
            for comm in communities:
                # 创建社区节点
                comm_dict = comm.to_dict()
                # Neo4j不支持Map数组，将findings转换为JSON字符串
                if comm_dict.get('findings') and isinstance(comm_dict['findings'], list):
                    comm_dict['findings'] = json.dumps(comm_dict['findings'], ensure_ascii=False)

                session.run("""
                    MERGE (c:Community {id: $id})
                    SET c.title = $title,
                        c.level = $level,
                        c.summary = $summary,
                        c.full_content = $full_content,
                        c.rank = $rank,
                        c.rank_explanation = $rank_explanation,
                        c.findings = $findings,
                        c.entity_ids = $entity_ids,
                        c.relationship_ids = $relationship_ids,
                        c.parent_id = $parent_id,
                        c.children_ids = $children_ids
                """, **comm_dict)

                # 创建社区到实体的关系
                for entity_id in comm.entity_ids:
                    session.run("""
                        MATCH (c:Community {id: $comm_id})
                        MATCH (e:Entity {id: $entity_id})
                        MERGE (c)-[:HAS_ENTITY]->(e)
                    """, comm_id=comm.id, entity_id=entity_id)

                # 创建社区层次关系
                if comm.parent_id:
                    session.run("""
                        MATCH (parent:Community {id: $parent_id})
                        MATCH (child:Community {id: $child_id})
                        MERGE (parent)-[:HAS_SUBCOMMUNITY]->(child)
                    """, parent_id=comm.parent_id, child_id=comm.id)

        logger.info(f"✅ 写入 {len(communities)} 个社区")

    def create_indexes(self):
        """创建索引以提高查询性能"""
        with self.driver.session() as session:
            # 通用实体索引（Entity标签覆盖所有实体）
            session.run("CREATE INDEX entity_id_index IF NOT EXISTS FOR (e:Entity) ON (e.id)")
            session.run("CREATE INDEX entity_name_index IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            session.run("CREATE INDEX entity_type_index IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)")

            # 具体类型标签索引
            for type_name in EntityRelationshipExtractor.ENTITY_TYPES:
                label = self._sanitize_label(type_name)
                try:
                    session.run(f"CREATE INDEX {label.lower()}_name_index IF NOT EXISTS FOR (n:{label}) ON (n.name)")
                except Exception as e:
                    logger.warning(f"创建索引 {label} 失败: {e}")

            # 社区索引
            session.run("CREATE INDEX community_id_index IF NOT EXISTS FOR (c:Community) ON (c.id)")
            session.run("CREATE INDEX community_level_index IF NOT EXISTS FOR (c:Community) ON (c.level)")

            logger.info("✅ 创建索引完成")

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.driver.session() as session:
            stats = {}

            # 实体统计
            result = session.run("MATCH (e:Entity) RETURN count(e) as count")
            stats["entity_count"] = result.single()["count"]

            # 按实体类型统计
            result = session.run("""
                MATCH (e:Entity)
                RETURN e.entity_type as entity_type, count(e) as count
                ORDER BY count(e) DESC
            """)
            stats["entities_by_type"] = {r["entity_type"]: r["count"] for r in result}

            # 关系统计（统计所有关系类型）
            result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
            stats["relationship_count"] = result.single()["count"]

            # 按关系类型统计
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count(r) DESC
            """)
            stats["relationships_by_type"] = {r["rel_type"]: r["count"] for r in result}

            # 社区统计
            result = session.run("MATCH (c:Community) RETURN count(c) as count")
            stats["community_count"] = result.single()["count"]

            # 按层级的社区统计
            result = session.run("""
                MATCH (c:Community)
                RETURN c.level as level, count(c) as count
                ORDER BY level
            """)
            stats["communities_by_level"] = {r["level"]: r["count"] for r in result}

            return stats

    def create_vector_indexes(self, dimension: int):
        """创建向量索引（用于向量检索）"""
        with self.driver.session() as session:
            try:
                session.run("""
                    CREATE VECTOR INDEX entity_embedding_index IF NOT EXISTS
                    FOR (e:Entity)
                    ON e.embedding
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                """, dimensions=dimension)
                logger.info("✅ 创建 entity_embedding_index 向量索引")
            except Exception as e:
                logger.warning(f"创建 entity_embedding_index 失败: {e}")

            try:
                session.run("""
                    CREATE VECTOR INDEX community_embedding_index IF NOT EXISTS
                    FOR (c:Community)
                    ON c.embedding
                    OPTIONS {
                        indexConfig: {
                            `vector.dimensions`: $dimensions,
                            `vector.similarity_function`: 'cosine'
                        }
                    }
                """, dimensions=dimension)
                logger.info("✅ 创建 community_embedding_index 向量索引")
            except Exception as e:
                logger.warning(f"创建 community_embedding_index 失败: {e}")

    def update_entity_embeddings(self, entities_with_embeddings: List[Dict]):
        """批量更新实体的embedding属性"""
        if not entities_with_embeddings:
            return 0

        updated = 0
        with self.driver.session() as session:
            for item in entities_with_embeddings:
                if item.get('embedding') is None:
                    continue
                try:
                    session.run("""
                        MATCH (e:Entity {id: $id})
                        SET e.embedding = $embedding,
                            e.embedding_model = $model
                    """, id=item['id'], embedding=item['embedding'], model=item['model'])
                    updated += 1
                except Exception as e:
                    logger.warning(f"更新实体 {item['id']} 的embedding失败: {e}")

        logger.info(f"✅ 更新 {updated} 个实体的向量嵌入")
        return updated

    def update_community_embeddings(self, communities_with_embeddings: List[Dict]):
        """批量更新社区的embedding属性"""
        if not communities_with_embeddings:
            return 0

        updated = 0
        with self.driver.session() as session:
            for item in communities_with_embeddings:
                if item.get('embedding') is None:
                    continue
                try:
                    session.run("""
                        MATCH (c:Community {id: $id})
                        SET c.embedding = $embedding,
                            c.embedding_model = $model
                    """, id=item['id'], embedding=item['embedding'], model=item['model'])
                    updated += 1
                except Exception as e:
                    logger.warning(f"更新社区 {item['id']} 的embedding失败: {e}")

        logger.info(f"✅ 更新 {updated} 个社区的向量嵌入")
        return updated

    def _sanitize_rel_type(self, rel_type: str) -> str:
        """清理关系类型名称（Neo4j要求大写字母开头）"""
        # 移除特殊字符，转换为大写
        return re.sub(r'[^a-zA-Z0-9_]', '_', rel_type).upper()


# ==================== 主索引构建器 ====================

class GraphRAGIndexer:
    """
    GraphRAG索引构建器

    整合所有组件，构建完整的GraphRAG索引
    支持断点续传功能
    """

    def __init__(self, config: GraphRAGIndexerConfig):
        self.config = config

        # 初始化各组件
        self.pdf_parser = PDFParser()
        self.text_chunker = TextChunker(config.chunk_size, config.chunk_overlap)
        self.entity_extractor = EntityRelationshipExtractor(config)
        self.community_detector = CommunityDetector(config)
        self.report_generator = CommunityReportGenerator(config)
        self.data_writer = Neo4jDataWriter(config)

        # 初始化Embedding生成器
        self.embedding_generator = EmbeddingGenerator(config)
        self.embedding_generator_ref = self.embedding_generator if self.embedding_generator.ready else None

        # 初始化检查点管理器
        self.checkpoint_manager = CheckpointManager(config.checkpoint_dir)

        logger.info("🚀 GraphRAG索引构建器初始化完成（支持断点续传）")

    def _generate_embeddings(self, entity_list: List[Entity], communities: List[Community]) -> Tuple[int, int]:
        """
        生成实体和社区的向量嵌入

        参数:
            entity_list: 实体列表
            communities: 社区列表

        返回:
            (成功更新的实体数, 成功更新的社区数)
        """
        gen = self.embedding_generator
        model_name = f"qwen/{gen.model_name}"

        # 1. 创建向量索引
        logger.info("   创建向量索引...")
        self.data_writer.create_vector_indexes(gen.dimension)

        # 2. 生成实体向量
        logger.info(f"   生成 {len(entity_list)} 个实体的向量嵌入...")
        entity_embeddings = []
        for i, entity in enumerate(entity_list):
            # 用 name + description 作为嵌入文本
            text = f"{entity.name} {entity.description}"
            embedding = gen.generate_embedding(text)
            entity_embeddings.append({
                'id': entity.id,
                'embedding': embedding,
                'model': model_name
            })
            if (i + 1) % gen.batch_size == 0 or (i + 1) == len(entity_list):
                logger.info(f"   实体向量进度: {i + 1}/{len(entity_list)}")

        entity_emb_count = self.data_writer.update_entity_embeddings(entity_embeddings)

        # 3. 生成社区向量
        logger.info(f"   生成 {len(communities)} 个社区的向量嵌入...")
        community_embeddings = []
        for i, comm in enumerate(communities):
            # 用 title + summary 作为嵌入文本
            text = f"{comm.title} {comm.summary}"
            embedding = gen.generate_embedding(text)
            community_embeddings.append({
                'id': comm.id,
                'embedding': embedding,
                'model': model_name
            })
            if (i + 1) % gen.batch_size == 0 or (i + 1) == len(communities):
                logger.info(f"   社区向量进度: {i + 1}/{len(communities)}")

        community_emb_count = self.data_writer.update_community_embeddings(community_embeddings)

        return entity_emb_count, community_emb_count

    def build_index(self, pdf_path: str, clear_existing: bool = False, force_restart: bool = False) -> Dict[str, Any]:
        """
        构建完整的GraphRAG索引（支持断点续传）

        流程：
        1. 解析PDF文档
        2. 分块处理文本
        3. 提取实体和关系
        4. 检测社区层次
        5. 生成社区报告
        6. 写入Neo4j数据库

        参数:
            pdf_path: PDF文件路径
            clear_existing: 是否清除Neo4j中的现有数据
            force_restart: 是否强制重新开始（忽略已有检查点）
        """
        logger.info(f"📄 开始处理文档: {pdf_path}")

        # 尝试加载已有检查点
        checkpoint = None
        if not force_restart:
            checkpoint = self.checkpoint_manager.load(pdf_path)

        if checkpoint and checkpoint.current_step == "extracting":
            # 从检查点恢复（提取阶段）
            logger.info("🔄 从检查点恢复进度...")
            return self._resume_from_checkpoint(checkpoint, pdf_path, clear_existing)

        if checkpoint and checkpoint.current_step == "writing":
            # 从检查点恢复（写入阶段 - 只需要写入Neo4j）
            logger.info("🔄 从检查点恢复进度（写入阶段）...")
            return self._resume_from_writing(checkpoint, pdf_path, clear_existing)

        # 创建新检查点
        checkpoint = Checkpoint(
            pdf_path=pdf_path,
            current_step="parsing",
            chunk_index=0,
            total_chunks=0
        )

        # 1. 解析PDF
        logger.info("📖 步骤1: 解析PDF文档...")
        raw_chunks = self.pdf_parser.parse(pdf_path)
        logger.info(f"   提取到 {len(raw_chunks)} 个原始文本块")

        # 2. 分块处理
        logger.info("📝 步骤2: 分块处理文本...")
        chunks = self.text_chunker.chunk(raw_chunks, self.config)
        logger.info(f"   生成 {len(chunks)} 个文本块")

        # 更新检查点
        logger.info("   保存检查点数据...")
        checkpoint.current_step = "extracting"
        checkpoint.total_chunks = len(chunks)
        try:
            checkpoint.chunks_data = [asdict(c) for c in chunks]
            logger.info(f"   检查点数据已准备，共 {len(checkpoint.chunks_data)} 个块")
        except Exception as e:
            logger.error(f"   保存检查点数据失败: {e}")
            checkpoint.chunks_data = []

        # 3. 提取实体和关系（支持断点续传）
        logger.info("🔍 步骤3: 提取实体和关系...")
        logger.info(f"   这将需要较长时间，共 {len(chunks)} 个块...")
        all_entities = {}
        all_relationships = {}

        for i, chunk in enumerate(chunks):
            if (i + 1) % 10 == 0 or i == 0:
                logger.info(f"   处理块 {i+1}/{len(chunks)}...")
            try:
                entities, relationships = self.entity_extractor.extract_from_chunk(chunk)
            except Exception as e:
                logger.warning(f"   块 {i+1} 提取失败: {e}，跳过")
                entities = []
                relationships = []

            # 合并实体（去重）
            for entity in entities:
                if entity.id in all_entities:
                    existing = all_entities[entity.id]
                    existing.source_chunks.extend(entity.source_chunks)
                    if not existing.description and entity.description:
                        existing.description = entity.description
                else:
                    all_entities[entity.id] = entity

            # 合并关系（去重）
            for rel in relationships:
                if rel.id in all_relationships:
                    existing = all_relationships[rel.id]
                    existing.source_chunks.extend(rel.source_chunks)
                    existing.weight += 1
                else:
                    all_relationships[rel.id] = rel

            # 更新检查点
            checkpoint.chunk_index = i + 1
            checkpoint.entities = [asdict(e) for e in all_entities.values()]
            checkpoint.relationships = [asdict(r) for r in all_relationships.values()]

            # 按间隔保存检查点
            if (i + 1) % self.config.checkpoint_interval == 0:
                self.checkpoint_manager.save(checkpoint)

        entity_list = list(all_entities.values())
        relationship_list = list(all_relationships.values())
        logger.info(f"   提取到 {len(entity_list)} 个实体, {len(relationship_list)} 个关系")

        # 更新检查点步骤
        checkpoint.current_step = "detecting"
        self.checkpoint_manager.save(checkpoint)

        # 4. 社区检测
        logger.info("🏘️ 步骤4: 检测社区层次...")
        communities = self.community_detector.detect_communities(entity_list, relationship_list)
        logger.info(f"   检测到 {len(communities)} 个社区")

        # 更新检查点
        checkpoint.current_step = "reporting"
        checkpoint.communities = [asdict(c) for c in communities]
        self.checkpoint_manager.save(checkpoint)

        # 5. 生成社区报告
        logger.info("📋 步骤5: 生成社区报告...")
        success_count = 0
        fail_count = 0
        for i, comm in enumerate(communities):
            logger.info(f"   生成报告 {i+1}/{len(communities)}: {comm.title}")
            _, success = self.report_generator.generate_report(comm, entity_list, relationship_list)
            if success:
                success_count += 1
            else:
                fail_count += 1

        logger.info(f"   ✅ 成功: {success_count}, ❌ 失败: {fail_count}")

        # 保存失败的社区ID
        if CommunityReportGenerator.get_failed_communities():
            CommunityReportGenerator.save_failed_communities_to_file("failed_communities.txt")

        # 更新检查点
        checkpoint.current_step = "writing"
        self.checkpoint_manager.save(checkpoint)

        # 6. 写入Neo4j
        logger.info("💾 步骤6: 写入Neo4j数据库...")
        if clear_existing:
            self.data_writer.clear_existing_data()

        self.data_writer.create_indexes()
        self.data_writer.write_entities(entity_list)
        self.data_writer.write_relationships(relationship_list)
        self.data_writer.write_communities(communities)

        # 7. 生成向量嵌入
        entity_emb_count = 0
        community_emb_count = 0
        if self.embedding_generator_ref:
            logger.info("🔢 步骤7: 生成向量嵌入...")
            entity_emb_count, community_emb_count = self._generate_embeddings(entity_list, communities)
            logger.info(f"   实体向量: {entity_emb_count}, 社区向量: {community_emb_count}")
        else:
            logger.info("ℹ️ 跳过向量生成（未配置 Embedding 服务）")

        # 获取统计信息
        stats = self.data_writer.get_statistics()

        # 保留检查点（不再清除，方便后续判断是否需要重建）

        logger.info("✅ 索引构建完成!")
        logger.info(f"   📊 统计信息: {stats}")

        return {
            "pdf_path": pdf_path,
            "chunks": len(chunks),
            "entities": len(entity_list),
            "relationships": len(relationship_list),
            "communities": len(communities),
            "entity_embeddings": entity_emb_count,
            "community_embeddings": community_emb_count,
            "statistics": stats
        }

    def _resume_from_checkpoint(self, checkpoint: Checkpoint, pdf_path: str, clear_existing: bool) -> Dict[str, Any]:
        """从检查点恢复进度"""
        logger.info(f"📍 从第 {checkpoint.chunk_index}/{checkpoint.total_chunks} 个块继续...")

        # 重建chunks
        chunks = []
        for chunk_data in checkpoint.chunks_data:
            chunk = TextChunk(
                id=chunk_data['id'],
                content=chunk_data['content'],
                source=chunk_data.get('source', pdf_path),
                page_number=chunk_data.get('page_number', 0),
                order=chunk_data.get('order', 0)
            )
            chunks.append(chunk)

        # 恢复已提取的实体和关系
        all_entities = {}
        all_relationships = {}

        for entity_dict in checkpoint.entities:
            entity = Entity(
                id=entity_dict['id'],
                name=entity_dict['name'],
                entity_type=entity_dict['entity_type'],
                description=entity_dict.get('description', ''),
                source_chunks=entity_dict.get('source_chunks', [])
            )
            all_entities[entity.id] = entity

        for rel_dict in checkpoint.relationships:
            rel = Relationship(
                id=rel_dict['id'],
                source_id=rel_dict['source_id'],
                target_id=rel_dict['target_id'],
                relationship_type=rel_dict['relationship_type'],
                description=rel_dict.get('description', ''),
                weight=rel_dict.get('weight', 1.0),
                source_chunks=rel_dict.get('source_chunks', [])
            )
            all_relationships[rel.id] = rel

        # 继续处理剩余的chunks
        logger.info(f"🔍 继续提取实体和关系...")
        for i in range(checkpoint.chunk_index, len(chunks)):
            logger.info(f"   处理块 {i+1}/{len(chunks)}...")
            entities, relationships = self.entity_extractor.extract_from_chunk(chunks[i])

            # 合并实体
            for entity in entities:
                if entity.id in all_entities:
                    existing = all_entities[entity.id]
                    existing.source_chunks.extend(entity.source_chunks)
                    if not existing.description and entity.description:
                        existing.description = entity.description
                else:
                    all_entities[entity.id] = entity

            # 合并关系
            for rel in relationships:
                if rel.id in all_relationships:
                    existing = all_relationships[rel.id]
                    existing.source_chunks.extend(rel.source_chunks)
                    existing.weight += 1
                else:
                    all_relationships[rel.id] = rel

            # 更新检查点
            checkpoint.chunk_index = i + 1
            checkpoint.entities = [asdict(e) for e in all_entities.values()]
            checkpoint.relationships = [asdict(r) for r in all_relationships.values()]

            if (i + 1) % self.config.checkpoint_interval == 0:
                self.checkpoint_manager.save(checkpoint)

        entity_list = list(all_entities.values())
        relationship_list = list(all_relationships.values())
        logger.info(f"   提取到 {len(entity_list)} 个实体, {len(relationship_list)} 个关系")

        # 后续步骤与正常流程相同
        checkpoint.current_step = "detecting"
        self.checkpoint_manager.save(checkpoint)

        logger.info("🏘️ 步骤4: 检测社区层次...")
        communities = self.community_detector.detect_communities(entity_list, relationship_list)
        logger.info(f"   检测到 {len(communities)} 个社区")

        checkpoint.current_step = "reporting"
        checkpoint.communities = [asdict(c) for c in communities]
        self.checkpoint_manager.save(checkpoint)

        logger.info("📋 步骤5: 生成社区报告...")
        success_count = 0
        fail_count = 0
        for i, comm in enumerate(communities):
            logger.info(f"   生成报告 {i+1}/{len(communities)}: {comm.title}")
            _, success = self.report_generator.generate_report(comm, entity_list, relationship_list)
            if success:
                success_count += 1
            else:
                fail_count += 1

        logger.info(f"   ✅ 成功: {success_count}, ❌ 失败: {fail_count}")

        # 保存失败的社区ID
        if CommunityReportGenerator.get_failed_communities():
            CommunityReportGenerator.save_failed_communities_to_file("failed_communities.txt")

        checkpoint.current_step = "writing"
        self.checkpoint_manager.save(checkpoint)

        logger.info("💾 步骤6: 写入Neo4j数据库...")
        if clear_existing:
            self.data_writer.clear_existing_data()

        self.data_writer.create_indexes()
        self.data_writer.write_entities(entity_list)
        self.data_writer.write_relationships(relationship_list)
        self.data_writer.write_communities(communities)

        # 7. 生成向量嵌入
        entity_emb_count = 0
        community_emb_count = 0
        if self.embedding_generator_ref:
            logger.info("🔢 步骤7: 生成向量嵌入...")
            entity_emb_count, community_emb_count = self._generate_embeddings(entity_list, communities)
            logger.info(f"   实体向量: {entity_emb_count}, 社区向量: {community_emb_count}")
        else:
            logger.info("ℹ️ 跳过向量生成（未配置 Embedding 服务）")

        stats = self.data_writer.get_statistics()

        logger.info("✅ 索引构建完成!")
        logger.info(f"   📊 统计信息: {stats}")

        return {
            "pdf_path": pdf_path,
            "chunks": checkpoint.total_chunks,
            "entities": len(entity_list),
            "relationships": len(relationship_list),
            "communities": len(communities),
            "entity_embeddings": entity_emb_count,
            "community_embeddings": community_emb_count,
            "statistics": stats
        }

    def _resume_from_writing(self, checkpoint: Checkpoint, pdf_path: str, clear_existing: bool) -> Dict[str, Any]:
        """从写入阶段恢复 - 直接写入Neo4j"""
        logger.info("📍 从写入阶段恢复，跳过数据处理...")

        # 恢复实体
        entity_list = []
        for entity_dict in checkpoint.entities:
            entity = Entity(
                id=entity_dict['id'],
                name=entity_dict['name'],
                entity_type=entity_dict['entity_type'],
                description=entity_dict.get('description', ''),
                source_chunks=entity_dict.get('source_chunks', [])
            )
            entity_list.append(entity)

        # 恢复关系
        relationship_list = []
        for rel_dict in checkpoint.relationships:
            rel = Relationship(
                id=rel_dict['id'],
                source_id=rel_dict['source_id'],
                target_id=rel_dict['target_id'],
                relationship_type=rel_dict['relationship_type'],
                description=rel_dict.get('description', ''),
                weight=rel_dict.get('weight', 1.0),
                source_chunks=rel_dict.get('source_chunks', [])
            )
            relationship_list.append(rel)

        # 恢复社区
        communities = []
        for comm_dict in checkpoint.communities:
            comm = Community(
                id=comm_dict['id'],
                title=comm_dict.get('title', ''),
                level=comm_dict.get('level', 0),
                summary=comm_dict.get('summary', ''),
                full_content=comm_dict.get('full_content', ''),
                rank=comm_dict.get('rank', 0.0),
                rank_explanation=comm_dict.get('rank_explanation', ''),
                findings=comm_dict.get('findings', []),
                entity_ids=comm_dict.get('entity_ids', []),
                relationship_ids=comm_dict.get('relationship_ids', []),
                parent_id=comm_dict.get('parent_id'),
                children_ids=comm_dict.get('children_ids', [])
            )
            communities.append(comm)

        logger.info(f"   恢复了 {len(entity_list)} 个实体, {len(relationship_list)} 个关系, {len(communities)} 个社区")

        # 直接写入Neo4j
        logger.info("💾 步骤6: 写入Neo4j数据库...")
        if clear_existing:
            self.data_writer.clear_existing_data()

        self.data_writer.create_indexes()
        self.data_writer.write_entities(entity_list)
        self.data_writer.write_relationships(relationship_list)
        self.data_writer.write_communities(communities)

        # 7. 生成向量嵌入
        entity_emb_count = 0
        community_emb_count = 0
        if self.embedding_generator_ref:
            logger.info("🔢 步骤7: 生成向量嵌入...")
            entity_emb_count, community_emb_count = self._generate_embeddings(entity_list, communities)
            logger.info(f"   实体向量: {entity_emb_count}, 社区向量: {community_emb_count}")
        else:
            logger.info("ℹ️ 跳过向量生成（未配置 Embedding 服务）")

        stats = self.data_writer.get_statistics()

        logger.info("✅ 索引构建完成!")
        logger.info(f"   📊 统计信息: {stats}")

        return {
            "pdf_path": pdf_path,
            "chunks": checkpoint.total_chunks,
            "entities": len(entity_list),
            "relationships": len(relationship_list),
            "communities": len(communities),
            "entity_embeddings": entity_emb_count,
            "community_embeddings": community_emb_count,
            "statistics": stats
        }

    def close(self):
        """关闭所有连接"""
        self.data_writer.close()
        logger.info("👋 索引构建器已关闭")


# ==================== 主函数 ====================

def main():
    """主函数"""
    print("=" * 60)
    print("🚀 GraphRAG社区层次索引构建器")
    print("   仿照Microsoft GraphRAG架构（支持断点续传）")
    print("=" * 60)

    # 配置
    config = GraphRAGIndexerConfig()

    # PDF文件路径
    pdf_path = r"C:\Users\lenovo\Desktop\aigc\data\cn.pdf"

    if not os.path.exists(pdf_path):
        print(f"❌ PDF文件不存在: {pdf_path}")
        return

    # 创建索引构建器
    indexer = GraphRAGIndexer(config)

    # 检查是否有检查点
    force_restart = False
    if indexer.checkpoint_manager.exists():
        print("\n📂 发现未完成的检查点！")
        print("   是否从检查点继续？(y=继续/n=重新开始): ", end="")
        choice = input().strip().lower()
        force_restart = (choice != 'y')
        if not force_restart:
            print("✅ 将从检查点恢复进度...")
        else:
            print("🔄 将重新开始并清除检查点...")

    try:
        # 询问是否清除现有数据
        print("\n⚠️ 是否清除Neo4j中的现有数据？(y/n): ", end="")
        choice = input().strip().lower()
        clear_existing = choice == 'y'

        # 构建索引
        result = indexer.build_index(pdf_path, clear_existing=clear_existing, force_restart=force_restart)

        print("\n" + "=" * 60)
        print("📊 索引构建结果")
        print("=" * 60)
        print(f"📄 PDF文件: {result['pdf_path']}")
        print(f"📝 文本块: {result['chunks']}")
        print(f"🔍 实体: {result['entities']}")
        print(f"🔗 关系: {result['relationships']}")
        print(f"🏘️ 社区: {result['communities']}")

        if result.get('statistics'):
            print("\n📈 数据库统计:")
            for key, value in result['statistics'].items():
                print(f"   - {key}: {value}")

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
        print("💡 进度已保存到检查点，下次运行可继续")
    except Exception as e:
        import traceback
        print(f"\n❌ 发生错误: {e}")
        traceback.print_exc()
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
