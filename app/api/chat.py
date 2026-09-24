"""唯一对外业务接口：POST /api/chat。"""

import logging

from fastapi import APIRouter, HTTPException

from app.engine.graph import run_graph
from app.engine.outputs import DialogueOutput, TERMINAL_OUTPUTS
from app.integrations.llm_api import LLMAPIError
from app.schemas.chat import ChatRequest, ChatResponse
from app.session.store import SessionBusyError, SessionStoreError, session_store


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.resolved_session_id()
    try:
        with session_store.lock(session_id, tenant_id=request.tenant_id):
            return _chat_locked(request, session_id)
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionStoreError as exc:
        logger.exception("会话存储失败: %s", exc)
        raise HTTPException(status_code=503, detail="会话存储暂时不可用") from exc


def _chat_locked(request: ChatRequest, session_id: str) -> ChatResponse:
    """在同一会话的分布式锁内完成一次完整的读取、决策和保存。"""

    session = session_store.get(session_id, tenant_id=request.tenant_id)
    request_history = request.engine_history()
    history = request_history or session.history
    # callInfo 是独立的跨轮共享状态，不与业务槽位 context 混存。
    # 客户端本轮显式传入时更新；省略时优先复用会话中已保存的通话信息。
    request_call_info = request.call_info.model_dump()
    call_info = (
        request_call_info
        if "call_info" in request.model_fields_set or not session.call_info
        else session.call_info
    )
    try:
        result = run_graph(
            {
                "session_id": session_id,
                "message": request.current_user_text,
                "history": history,
                "tenant_id": request.tenant_id,
                "call_info": call_info,
                "system_prompt": request.system_prompt,
                "config": request.config.model_dump(),
                "business": session.business,
                "plugin_state": session.plugin_state,
                "context": session.context,
                "unrecognized_count": session.unrecognized_count,
            }
        )
    except LLMAPIError as exc:
        logger.exception("模型调用失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc
    except Exception as exc:
        logger.exception("对话引擎执行失败")
        raise HTTPException(status_code=500, detail="对话引擎执行失败") from exc

    reply_text = result["reply"]
    output = result.get("out", DialogueOutput.CHAT)
    if output in TERMINAL_OUTPUTS:
        # HUMAN/END 的本轮回复仍正常返回，但服务端不再保留任何旧会话内容。
        session_store.delete(session_id, tenant_id=request.tenant_id)
    else:
        session_store.save(
            session_id,
            business=result["business"],
            plugin_state=result.get("plugin_state"),
            call_info=result.get("call_info", call_info),
            context=result.get("context", {}),
            user_message=request.current_user_text,
            assistant_message=reply_text,
            tenant_id=request.tenant_id,
            unrecognized_count=result.get("unrecognized_count", 0),
        )
    data = {
        key: value
        for key, value in result.get("action_result", {}).items()
        if key not in {"ok", "error"}
    }
    if output == DialogueOutput.HUMAN:
        data.update(
            {
                "unrecognized_count": result.get("unrecognized_count", 0),
                "handoff_reason": result.get("handoff_reason"),
            }
        )
    return ChatResponse(
        session_id=session_id,
        reply=reply_text,
        business=result["business"],
        intent=result["intent"],
        action=result.get("action"),
        out=output,
        data=data,
    )
