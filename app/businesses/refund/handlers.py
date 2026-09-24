"""退款插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from typing import Any

from app.engine.action_result import ActionResult, action_success
from app.services.order_lookup import query_order


def submit_refund(state: dict[str, Any]) -> ActionResult:
    """组装退款提交参数；实际退款由调用方后续执行。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    refund_params = {
        "order_no": str(values["order_no"]).upper(),
        "reason": str(values.get("reason") or "用户申请"),
    }
    return action_success(**refund_params, eta="1到3个工作日")


__all__ = ["query_order", "submit_refund"]
