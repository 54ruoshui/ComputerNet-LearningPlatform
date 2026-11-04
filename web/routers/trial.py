"""前测-后测试用评估 API"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from web import deps
from web.deps import get_trial_service, require_admin
from web.schemas import (
    TrialCreateRequest,
    TrialJoinRequest,
    TrialParticipantRequest,
    TrialTestStartRequest,
    TrialTestSubmitRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trial", tags=["trial"])


@router.post("/create")
def create_trial(req: TrialCreateRequest, _admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        return service.create_trial(req.name, per_layer=req.per_layer, seed=req.seed)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("创建试用批次失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
def list_trials(_admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        return {"trials": service.list_trials()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取试用批次列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{trial_id}/participants")
def add_trial_participant(trial_id: str, req: TrialParticipantRequest, _admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        participant = service.add_participant(trial_id, req.code)
        return participant
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("登记被试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{trial_id}/participants")
def list_trial_participants(trial_id: str, _admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        return {"participants": service.list_participants(trial_id)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("获取被试列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/join")
def join_trial(req: TrialJoinRequest, request: Request, service=Depends(get_trial_service)):
    try:
        result = service.join(req.code, req.session_id)
        # 参试码即会话凭证：把协商出的会话归属改绑到当前访客
        # （换设备 join 时会话历史随人走；共用电脑换码则拿到新发会话）
        tracker = deps._get_tracker()
        visitor = request.scope.get("aigc_visitor") or request.cookies.get("aigc_visitor")
        if tracker and visitor and result.get("session_id"):
            try:
                tracker.claim_session(result["session_id"], visitor)
            except Exception as e:
                logger.warning("会话归属改绑失败", error=str(e))
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("被试加入失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/start")
def start_trial_test(req: TrialTestStartRequest, service=Depends(get_trial_service)):
    try:
        return service.start_test(req.code, req.phase)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("开始测试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/submit")
def submit_trial_test(req: TrialTestSubmitRequest, service=Depends(get_trial_service)):
    try:
        return service.submit_test(req.test_id, req.answers, req.code)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("提交测试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{trial_id}/analysis")
def get_trial_analysis(trial_id: str, _admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        return service.get_analysis(trial_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("获取试用分析失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{trial_id}/report")
def generate_trial_report(trial_id: str, _admin: None = Depends(require_admin), service=Depends(get_trial_service)):
    try:
        report = service.generate_report(trial_id)
        if report is None:
            raise HTTPException(status_code=502, detail="报告生成失败，请稍后重试")
        return {"trial_id": trial_id, "report": report}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("生成试用评估报告失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
