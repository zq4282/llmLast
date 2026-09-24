"""与具体业务无关的未理解恢复和转人工策略。"""

from typing import Any

from app.engine.outputs import DialogueOutput

MAX_UNRECOGNIZED_ATTEMPTS = 3
ACTIVE_HANDOFF_STATUSES = frozenset({"HANDOFF_PENDING", "HUMAN"})

SYSTEM_FALLBACKS = {
    "unknown_first": "抱歉，我没太听明白。您可以换一种说法，或补充一下具体需要办理什么。",
    "unknown_second": (
        "抱歉，我还是没能理解。您可以再补充一次，也可以直接说“转人工”；"
        "如果仍无法识别，我将为您转接人工客服。"
    ),
    "unknown_handoff": (
        "还是没能理解您的意思，正在为您转接人工客服，本次沟通记录已同步，请稍候。"
    ),
    "human_handoff": "正在为您转接人工客服，本次沟通记录已同步，请稍候。",
    "human_waiting": "已为您申请转接人工客服，请稍候。",
    "unsupported_intent": "我理解了您的问题，但当前流程暂时无法处理，您可以换一个问题或转人工客服。",
}


def recovery_decision(state: dict[str, Any]) -> dict[str, Any] | None:
    """为未理解和人工接管生成统一决策；正常业务返回 None。"""

    status = str(state.get("conversation_status", "BOT"))
    current_count = max(0, int(state.get("unrecognized_count", 0)))
    plugin_state = state.get("plugin_state")

    if status in ACTIVE_HANDOFF_STATUSES:
        return _decision(
            reply_key="human_waiting",
            out=DialogueOutput.HUMAN,
            plugin_state=plugin_state,
            count=current_count,
            status=status,
            reason=state.get("handoff_reason"),
        )

    intent = state.get("intent")
    if intent == "human":
        return _decision(
            reply_key="human_handoff",
            out=DialogueOutput.HUMAN,
            plugin_state=plugin_state,
            count=0,
            status="HANDOFF_PENDING",
            reason="USER_REQUESTED",
        )

    if intent != "unknown":
        return None

    count = current_count + 1
    if count < MAX_UNRECOGNIZED_ATTEMPTS:
        return _decision(
            reply_key="unknown_first" if count == 1 else "unknown_second",
            out=DialogueOutput.CHAT,
            plugin_state=plugin_state,
            count=count,
            status="BOT",
            reason=None,
        )
    return _decision(
        reply_key="unknown_handoff",
        out=DialogueOutput.HUMAN,
        plugin_state=plugin_state,
        count=count,
        status="HANDOFF_PENDING",
        reason="CONSECUTIVE_UNRECOGNIZED",
    )


def _decision(
    *,
    reply_key: str,
    out: DialogueOutput,
    plugin_state: str | None,
    count: int,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "action": "none",
        "reply_key": reply_key,
        "next_plugin_state": plugin_state,
        "out": out,
        "use_fallback": True,
        "use_system_fallback": True,
        "unrecognized_count": count,
        "conversation_status": status,
        "handoff_reason": reason,
    }
