"""退款业务处理函数。"""

from typing import Any

from app.integrations import order_api, refund_api


def query_order(state: dict[str, Any]) -> dict[str, Any]:
    """退款流程中的订单校验能力，也可被后续动作表直接引用。"""

    order_id = state.get("slots", {}).get("order_id") or state.get("context", {}).get("order_id")
    order = order_api.query_order(order_id) if order_id else order_api.query_current_order()
    if not order.get("ok"):
        return order
    return {**order, "merchant": order.get("merchant", order.get("item", "未知商户"))}


def submit_refund(state: dict[str, Any]) -> dict[str, Any]:
    values = {**state.get("context", {}), **state.get("slots", {})}
    order_id = values.get("order_id")
    if not order_id:
        return {"ok": False, "error": "missing_order_id"}
    result = refund_api.submit_refund(order_id.upper(), values.get("reason", "用户申请"))
    return {**result, "eta": result.get("eta", "1-3 个工作日")}


def query_refund(state: dict[str, Any]) -> dict[str, Any]:
    return refund_api.query_refund(state["slots"]["order_id"].upper())
