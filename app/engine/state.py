"""LangGraph 在五个节点之间传递的状态。"""

from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    session_id: str
    message: str
    history: list[dict[str, str]]
    active_business: str | None
    business: str
    intent: str
    slots: dict[str, Any]
    context: dict[str, Any]
    action: str | None
    missing_slots: list[str]
    action_result: dict[str, Any]
    reply: str
    error: str | None
