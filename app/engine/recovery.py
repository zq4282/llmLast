"""用户的话听不懂或当前流程接不住时，决定如何提示和转人工。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.constants import (
    ActionName,
    ChatField,
    EngineIntent,
    FallbackKey,
    HandoffReason,
    RecoveryTrigger,
)
from app.engine.outputs import DialogueOutput
from app.engine.state import ReplySource

# 插件没有自己的提示话术时，使用这些通用回复。
SYSTEM_FALLBACKS = {
    FallbackKey.UNKNOWN_FIRST: "抱歉，我没太听明白。您可以换一种说法，或补充一下具体需要办理什么。",
    FallbackKey.UNKNOWN_SECOND: (
        "抱歉，我还是没能理解。您可以再补充一次，也可以直接说“转人工”；"
        "如果仍无法识别，我将为您转接人工客服。"
    ),
    FallbackKey.UNKNOWN_HANDOFF: (
        "还是没能理解您的意思，正在为您转接人工客服，本次沟通记录已同步，请稍候。"
    ),
    FallbackKey.HUMAN_HANDOFF: "正在为您转接人工客服，本次沟通记录已同步，请稍候。",
    FallbackKey.UNSUPPORTED_FIRST: "当前流程无法处理这句话，请根据上一条提示重新回答。",
    FallbackKey.UNSUPPORTED_SECOND: (
        "当前流程仍无法处理您的回复。您可以按上一条提示回答，或直接说“转人工”；"
        "如果仍无法继续，我将为您转接人工客服。"
    ),
    FallbackKey.UNSUPPORTED_HANDOFF: (
        "当前流程无法继续处理，正在为您转接人工客服，本次沟通记录已同步，请稍候。"
    ),
    FallbackKey.UNSUPPORTED_INTENT: "我理解了您的问题，但当前流程暂时无法处理，您可以换一个问题或转人工客服。",
}


@dataclass(frozen=True)
class RecoveryStep:
    """一次提示要说的话，以及这次是继续聊天还是转人工。"""

    reply: str
    out: DialogueOutput


# 默认先提示两次，仍无法继续时交给人工；插件可以配置自己的步骤。
DEFAULT_RECOVERY_STEPS = {
    RecoveryTrigger.UNKNOWN: (
        RecoveryStep(FallbackKey.UNKNOWN_FIRST, DialogueOutput.CHAT),
        RecoveryStep(FallbackKey.UNKNOWN_SECOND, DialogueOutput.CHAT),
        RecoveryStep(FallbackKey.UNKNOWN_HANDOFF, DialogueOutput.HUMAN),
    ),
    RecoveryTrigger.UNSUPPORTED: (
        RecoveryStep(FallbackKey.UNSUPPORTED_FIRST, DialogueOutput.CHAT),
        RecoveryStep(FallbackKey.UNSUPPORTED_SECOND, DialogueOutput.CHAT),
        RecoveryStep(FallbackKey.UNSUPPORTED_HANDOFF, DialogueOutput.HUMAN),
    ),
}


def recovery_decision(
    state: dict[str, Any],
    *,
    trigger: str | None = None,
    steps: Sequence[RecoveryStep] | None = None,
) -> dict[str, Any] | None:
    """决定这次该提醒、转人工，还是继续走正常业务。"""

    # 次数跟着当前业务流程走，同一流程连续失败才会逐步升级。
    current_count = max(0, int(state.get(ChatField.UNRECOGNIZED_COUNT, 0)))
    plugin_state = state.get(ChatField.PLUGIN_STATE)

    intent = state.get(ChatField.INTENT)
    if intent == EngineIntent.HUMAN:
        # 用户主动要人工时直接转，不再按“听不懂”的次数等待。
        return _decision(
            reply_key=FallbackKey.HUMAN_HANDOFF,
            out=DialogueOutput.HUMAN,
            plugin_state=plugin_state,
            count=0,
            reason=HandoffReason.USER_REQUESTED,
        )

    if trigger is None:
        if intent != EngineIntent.UNKNOWN:
            return None
        trigger = RecoveryTrigger.UNKNOWN
    elif trigger not in set(RecoveryTrigger):
        raise ValueError(f"未知恢复触发类型: {trigger}")

    configured_steps = tuple(DEFAULT_RECOVERY_STEPS[trigger] if steps is None else steps)
    if not configured_steps:
        raise ValueError(f"恢复步骤不能为空: {trigger}")
    count = current_count + 1
    # 会话里即使存了过大的次数，也只取最后一步，不让索引越界。
    step = configured_steps[min(count - 1, len(configured_steps) - 1)]
    is_handoff = step.out == DialogueOutput.HUMAN
    return _decision(
        reply_key=step.reply,
        out=step.out,
        plugin_state=plugin_state,
        count=count,
        reason=(
            (
                HandoffReason.CONSECUTIVE_UNRECOGNIZED
                if trigger == RecoveryTrigger.UNKNOWN
                else HandoffReason.CONSECUTIVE_UNSUPPORTED
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
        ChatField.ACTION: ActionName.NONE,
        ChatField.REPLY_KEY: reply_key,
        ChatField.NEXT_PLUGIN_STATE: plugin_state,
        ChatField.OUT: out,
        ChatField.REPLY_SOURCE: ReplySource.SYSTEM_FALLBACK,
        ChatField.UNRECOGNIZED_COUNT: count,
        ChatField.HANDOFF_REASON: reason,
    }
