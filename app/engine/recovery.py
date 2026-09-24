"""与具体业务无关的未理解恢复和转人工策略。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.outputs import DialogueOutput
from app.engine.state import ReplySource

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
    "unsupported_first": "当前流程无法处理这句话，请根据上一条提示重新回答。",
    "unsupported_second": (
        "当前流程仍无法处理您的回复。您可以按上一条提示回答，或直接说“转人工”；"
        "如果仍无法继续，我将为您转接人工客服。"
    ),
    "unsupported_handoff": (
        "当前流程无法继续处理，正在为您转接人工客服，本次沟通记录已同步，请稍候。"
    ),
    "unsupported_intent": "我理解了您的问题，但当前流程暂时无法处理，您可以换一个问题或转人工客服。",
}


@dataclass(frozen=True)
class RecoveryStep:
    """一次恢复尝试的话术和对外输出；步骤数量就是最大恢复次数。"""

    reply: str
    out: DialogueOutput


DEFAULT_RECOVERY_STEPS = {
    "unknown": (
        RecoveryStep("unknown_first", DialogueOutput.CHAT),
        RecoveryStep("unknown_second", DialogueOutput.CHAT),
        RecoveryStep("unknown_handoff", DialogueOutput.HUMAN),
    ),
    "unsupported": (
        RecoveryStep("unsupported_first", DialogueOutput.CHAT),
        RecoveryStep("unsupported_second", DialogueOutput.CHAT),
        RecoveryStep("unsupported_handoff", DialogueOutput.HUMAN),
    ),
}


def recovery_decision(
    state: dict[str, Any],
    *,
    trigger: str | None = None,
    steps: Sequence[RecoveryStep] | None = None,
) -> dict[str, Any] | None:
    """为未理解和人工接管生成统一决策；正常业务返回 None。"""

    current_count = max(0, int(state.get("unrecognized_count", 0)))
    plugin_state = state.get("plugin_state")

    intent = state.get("intent")
    if intent == "human":
        return _decision(
            reply_key="human_handoff",
            out=DialogueOutput.HUMAN,
            plugin_state=plugin_state,
            count=0,
            reason="USER_REQUESTED",
        )

    if trigger is None:
        if intent != "unknown":
            return None
        trigger = "unknown"
    elif trigger not in {"unknown", "unsupported"}:
        raise ValueError(f"未知恢复触发类型: {trigger}")

    configured_steps = tuple(DEFAULT_RECOVERY_STEPS[trigger] if steps is None else steps)
    if not configured_steps:
        raise ValueError(f"恢复步骤不能为空: {trigger}")
    count = current_count + 1
    # 最后一步应由配置声明为 HUMAN；min 仅用于防御脏会话计数。
    step = configured_steps[min(count - 1, len(configured_steps) - 1)]
    is_handoff = step.out == DialogueOutput.HUMAN
    return _decision(
        reply_key=step.reply,
        out=step.out,
        plugin_state=plugin_state,
        count=count,
        reason=(
            (
                "CONSECUTIVE_UNRECOGNIZED"
                if trigger == "unknown"
                else "CONSECUTIVE_UNSUPPORTED"
            )
            if is_handoff
            else None
        ),
    )


def _decision(
    *,
    reply_key: str,
    out: DialogueOutput,
    plugin_state: str | None,
    count: int,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "action": "none",
        "reply_key": reply_key,
        "next_plugin_state": plugin_state,
        "out": out,
        "reply_source": ReplySource.SYSTEM_FALLBACK,
        "unrecognized_count": count,
        "handoff_reason": reason,
    }
