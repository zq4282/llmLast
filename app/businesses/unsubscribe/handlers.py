"""退订插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from typing import Any

from app.services.order_lookup import query_order
from app.engine.action_result import ActionResult, action_success


def submit_unsubscribe(state: dict[str, Any]) -> ActionResult:
    """组装退订提交参数；实际退订由调用方后续执行。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    unsubscribe_params = {
        "order_no": str(values["order_no"]).upper(),
        "reason": str(values.get("reason") or "用户申请"),
    }
    return action_success(**unsubscribe_params, effective_time="立即生效")


__all__ = ["query_order", "submit_unsubscribe"]
