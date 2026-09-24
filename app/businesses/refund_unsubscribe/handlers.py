"""退款并退订插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from typing import Any

from app.businesses.refund.handlers import query_order
from app.engine.action_result import ActionResult, action_success


def submit_refund_unsubscribe(state: dict[str, Any]) -> ActionResult:
    """组装组合操作，并跳过当前会话中已经成功完成的部分。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    params = {
        "order_no": str(values["order_no"]).upper(),
        "reason": str(values.get("reason") or "用户申请"),
    }
    completed_outputs = {
        str(item.get("out"))
        for item in state.get("completed_actions", [])
        if item.get("status") == "SUCCESS"
    }
    refund_done = bool({"REFUND", "REFUND_UNSUBSCRIBE"} & completed_outputs)
    unsubscribe_done = bool({"UNSUBSCRIBE", "REFUND_UNSUBSCRIBE"} & completed_outputs)
    performed_actions = [
        action
        for action, done in (("refund", refund_done), ("unsubscribe", unsubscribe_done))
        if not done
    ]
    skipped_actions = [
        action
        for action, done in (("refund", refund_done), ("unsubscribe", unsubscribe_done))
        if done
    ]
    return action_success(
        **params,
        eta="1到3个工作日",
        effective_time="立即生效",
        performed_actions=performed_actions,
        skipped_actions=skipped_actions,
        refund_result=(
            "退款此前已经提交，本次不重复退款"
            if refund_done
            else "退款已提交，预计1到3个工作日到账"
        ),
        unsubscribe_result=(
            "订阅此前已经取消，本次不重复退订"
            if unsubscribe_done
            else "退订立即生效，后续不再自动续费"
        ),
    )


__all__ = ["query_order", "submit_refund_unsubscribe"]
