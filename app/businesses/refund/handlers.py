"""退款业务处理函数。"""

from typing import Any

from app.engine.action_result import (
    ActionResult,
    action_failure,
    action_success,
)
from app.integrations import order_api, refund_api


def query_order(state: dict[str, Any]) -> ActionResult:
    """退款流程中的订单校验能力，也可被后续动作表直接引用。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    call_info = state.get("call_info", {})

    query_params: dict[str, str] = {}
    if values.get("order_no"):
        query_params["order_no"] = str(values["order_no"])
    elif values.get("backup_phone"):
        query_params["phone"] = str(values["backup_phone"])
    elif call_info.get("caller"):
        query_params["phone"] = str(call_info["caller"])

    # 查询优先级：订单号 > 用户补充手机号 > 本次通话的主叫号码。
    order = order_api.query_order(**query_params)
    data = {
        key: value
        for key, value in order.items()
        if key not in {"ok", "error"}
    }
    if not order.get("ok"):
        return action_failure(str(order.get("error", "internal_error")), **data)
    data["merchant"] = order.get("merchant", order.get("item", "未知商户"))
    return action_success(**data)


def submit_refund(state: dict[str, Any]) -> ActionResult:
    values = {**state.get("context", {}), **state.get("slots", {})}
    order_no = values.get("order_no")
    if not order_no:
        return action_failure("missing_order_no")
    result = refund_api.submit_refund(order_no.upper(), values.get("reason", "用户申请"))
    data = {
        key: value
        for key, value in result.items()
        if key not in {"ok", "error"}
    }
    if not result.get("ok"):
        return action_failure(str(result.get("error", "internal_error")), **data)
    data.setdefault("eta", "1到3个工作日")
    return action_success(**data)


def query_refund(state: dict[str, Any]) -> ActionResult:
    result = refund_api.query_refund(state["slots"]["order_no"].upper())
    data = {
        key: value
        for key, value in result.items()
        if key not in {"ok", "error"}
    }
    if not result.get("ok"):
        return action_failure(str(result.get("error", "internal_error")), **data)
    return action_success(**data)
