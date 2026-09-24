"""转人工插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from typing import Any

from app.engine.action_result import ActionResult, action_failure, action_success


def record_reason(state: dict[str, Any]) -> ActionResult:
    """记录模型从用户原话中抽取的转人工原因。"""

    reason = state.get("slots", {}).get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return action_failure("missing_reason")
    return action_success(reason=reason.strip())


def record_insult(state: dict[str, Any]) -> ActionResult:
    """由程序累加辱骂次数，避免让模型猜测当前是第几次。"""

    previous_count = state.get("context", {}).get("insult_count", 0)
    try:
        insult_count = max(0, int(previous_count)) + 1
    except (TypeError, ValueError):
        insult_count = 1
    return action_success(sentiment="insult", insult_count=insult_count)
