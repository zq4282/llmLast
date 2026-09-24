"""退款业务处理函数。"""

from typing import Any

from app.integrations import order_api, refund_api


def _use_order_no(result: dict[str, Any]) -> dict[str, Any]:
    """将底层接口的 order_id 适配为退款插件统一使用的 order_no。"""

    normalized = dict(result)
    if "order_id" in normalized:
        normalized["order_no"] = normalized.pop("order_id")
    return normalized


def query_order(state: dict[str, Any]) -> dict[str, Any]:
    """退款流程中的订单校验能力，也可被后续动作表直接引用。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    order_no = values.get("order_no")
    backup_phone = values.get("backup_phone")
    if order_no:
        order = order_api.query_order(str(order_no))
    elif backup_phone:
        order = order_api.query_order_by_phone(str(backup_phone))
    else:
        order = order_api.query_current_order()
    order = _use_order_no(order)
    if not order.get("ok"):
        return order
    return {**order, "merchant": order.get("merchant", order.get("item", "未知商户"))}


def submit_refund(state: dict[str, Any]) -> dict[str, Any]:
    values = {**state.get("context", {}), **state.get("slots", {})}
    order_no = values.get("order_no")
    if not order_no:
        return {"ok": False, "error": "missing_order_no"}
    result = _use_order_no(
        refund_api.submit_refund(order_no.upper(), values.get("reason", "用户申请"))
    )
    return {**result, "eta": result.get("eta", "1-3 个工作日")}


def query_refund(state: dict[str, Any]) -> dict[str, Any]:
    return _use_order_no(refund_api.query_refund(state["slots"]["order_no"].upper()))
