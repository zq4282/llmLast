"""唯一对外业务接口：POST /api/chat。"""

from fastapi import APIRouter, HTTPException

from app.engine.graph import run_graph
from app.schemas.chat import ChatRequest, ChatResponse
from app.session.store import session_store


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.resolved_session_id()
    session = session_store.get(session_id)
    try:
        result = run_graph(
            {
                "session_id": session_id,
                "message": request.message,
                "history": session.history,
                "business": session.business,
                "plugin_state": session.plugin_state,
                "route_task": session.route_task,
                "route_confidence": session.route_confidence,
                "route_locked": session.route_locked,
                "context": session.context,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="对话引擎执行失败") from exc

    reply_text = result["reply"]
    session_store.save(
        session_id,
        business=result["business"],
        plugin_state=result.get("plugin_state"),
        route_task=result.get("route_task"),
        route_confidence=result.get("route_confidence"),
        route_locked=result.get("route_locked", False),
        context=result.get("context", {}),
        user_message=request.message,
        assistant_message=reply_text,
    )
    data = {
        key: value
        for key, value in result.get("action_result", {}).items()
        if key not in {"ok", "error"}
    }
    return ChatResponse(
        session_id=session_id,
        reply=reply_text,
        business=result["business"],
        intent=result["intent"],
        action=result.get("action"),
        out=result.get("out", "CHAT"),
        data=data,
    )
