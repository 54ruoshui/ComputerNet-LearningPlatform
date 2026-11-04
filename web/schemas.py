"""Pydantic 请求模型（带校验）"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: Optional[str] = None


class RegisterRequest(BaseModel):
    # 邮箱仅作登录标识（无 SMTP 验证），用正则校验避免引入 email-validator
    email: str = Field(..., min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(..., min_length=8, max_length=128)
    nickname: str = Field(..., min_length=1, max_length=20)
    role: Literal["student", "teacher", "admin"] = "student"
    # 教师/管理员注册邀请码（student 忽略）
    invite_code: str = Field(default="", max_length=64)
    session_id: Optional[str] = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    # 登录不做密码长度下限校验，避免泄露密码策略
    password: str = Field(..., min_length=1, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=64)


class SessionClaimRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)


class ExportRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="导出查询")


class GenerateImageRequest(BaseModel):
    graph_data: dict
    question: str = Field(..., min_length=1, max_length=500)


class MasteryRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entity_name: str = Field(..., min_length=1, max_length=200)
    mastered: bool


class MasteryBatchRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entities: List[Dict]


class QuizAnswerRequest(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=100)
    selected_answer: str = Field(..., pattern="^[ABCD]$")
    session_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=200)
    question: str = Field(default="", max_length=2000, description="被评价的提问文本")
    rating: int = Field(..., ge=0, le=1, description="1=满意 / 0=不满意")
    comment: str = Field(default="", max_length=500)


class QuizGenerateRequest(BaseModel):
    layer: Optional[str] = Field(default=None, pattern="^(物理层|数据链路层|网络层|传输层|应用层)$")
    chapter: Optional[str] = Field(default=None, max_length=10)
    difficulty: Optional[str] = Field(default=None, pattern="^(basic|medium|hard)$")
    count: int = Field(default=5, ge=1, le=10)
    seed_entities: Optional[List[str]] = Field(default=None, max_length=10)


class ExamSubmitRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    paper_id: str = Field(..., min_length=1, max_length=100)
    answers: Dict[str, str] = Field(..., description="题目ID→答案的映射，如 {'q1': 'A', 'q2': 'B'}")


class ReviewRecordRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entity_name: str = Field(..., min_length=1, max_length=200)
    quality: int = Field(..., ge=0, le=5, description="回忆质量 0-5")
    event_type: str = Field(default="manual", pattern="^(quiz_correct|quiz_wrong|manual)$")


class MCQuestionCreate(BaseModel):
    text: str = Field(..., min_length=2, max_length=1000)
    option_a: str = Field(..., min_length=1, max_length=500)
    option_b: str = Field(..., min_length=1, max_length=500)
    option_c: str = Field(..., min_length=1, max_length=500)
    option_d: str = Field(..., min_length=1, max_length=500)
    correct_answer: str = Field(..., pattern="^[ABCD]$")
    explanation: str = Field(..., min_length=1, max_length=2000)
    difficulty: str = Field(..., pattern="^(basic|medium|hard)$")
    layer: str = Field(..., pattern="^(物理层|数据链路层|网络层|传输层|应用层)$")
    references: Optional[List[str]] = []
    source: str = Field(default="manual", pattern="^(manual|ai)$",
                        description="题目来源：手动添加或 AI 生成")


class TrialCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="试用批次名称")
    per_layer: int = Field(default=4, ge=2, le=10, description="每层抽取题量（前测/后测各 per_layer 题）")
    seed: Optional[int] = Field(default=None, ge=0, le=999999, description="随机种子，固定则平行卷可复现")


class TrialParticipantRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=20, description="被试码，缺省自动生成")


class TrialJoinRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, description="被试码")
    session_id: str = Field(..., min_length=1, max_length=200, description="学习会话 ID")


class TrialTestStartRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, description="被试码")
    phase: str = Field(..., pattern="^(pre|post)$", description="pre=前测 / post=后测")


class TrialTestSubmitRequest(BaseModel):
    test_id: int = Field(..., ge=1)
    code: str = Field(..., min_length=2, max_length=20, description="被试码，须与开始测试时一致")
    answers: Dict[str, str] = Field(..., description="题目ID→答案映射，如 {'q1': 'A'}")


class WrongEntry(BaseModel):
    id: str
    text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: str
    explanation: str
    layer: str
    difficulty: str
    selected: str
    time: Optional[str] = None
    session_id: Optional[str] = None
