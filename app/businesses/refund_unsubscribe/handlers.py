"""退款并退订插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from typing import Any

from app.businesses.refund.handlers import query_order
from app.engine.action_result import ActionResult, action_success


def submit_refund_unsubscribe(state: dict[str, Any]) -> ActionResult:
    """组装退款并退订参数；实际业务操作由调用方后续执行。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    params = {
        "order_no": str(values["order_no"]).upper(),
        "reason": str(values.get("reason") or "用户申请"),
    }
    return action_success(
        **params,
        eta="1到3个工作日",
        effective_time="立即生效",
    )


__all__ = ["query_order", "submit_refund_unsubscribe"]
