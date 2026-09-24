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
    active_business = session.business if session.context.get("_pending_slots") else None
    try:
        result = run_graph(
            {
                "session_id": session_id,
                "message": request.message,
                "history": session.history,
                "active_business": active_business,
                "context": session.context,
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="对话引擎执行失败") from exc

    reply_text = result["reply"]
    session_store.save(
        session_id,
        business=result["business"],
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
        data=data,
    )
