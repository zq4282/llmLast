"""退款插件动作；plugin.yaml 中配置的业务方法统一在此实现。"""

from copy import deepcopy
from typing import Any

from app.engine.action_result import (
    ActionResult,
    action_failure,
    action_success,
)


_ORDERS = {
    "ORD202405010001": {
        "order_no": "ORD202405010001",
        "productName": "专业版年度会员订阅",
        "payStatus": "支付成功",
        "payTime": "2024-05-01 10:15:30",
        "payAmount": 299.00,
        "payMethod": "微信",
        "refundStatus": "未退款",
        "refundAmount": 0.00,
        "refundTime": None,
        "refundFailReason": None,
        "subscriptionStatus": "订阅中",
        "cancelTime": None,
    },
    "ORD202405020002": {
        "order_no": "ORD202405020002",
        "productName": "云存储扩容包 (100GB/月)",
        "payStatus": "支付成功",
        "payTime": "2024-05-02 14:20:10",
        "payAmount": 15.00,
        "payMethod": "支付宝",
        "refundStatus": "退款成功",
        "refundAmount": 15.00,
        "refundTime": "2024-05-03 09:12:00",
        "refundFailReason": None,
        "subscriptionStatus": "已退订",
        "cancelTime": "2024-05-03 09:10:00",
    },
    "ORD202405030003": {
        "order_no": "ORD202405030003",
        "productName": "高级团队版按月订阅",
        "payStatus": "支付成功",
        "payTime": "2024-05-03 16:45:00",
        "payAmount": 99.00,
        "payMethod": "微信",
        "refundStatus": "退款失败",
        "refundAmount": 0.00,
        "refundTime": None,
        "refundFailReason": "已超过7天无理由退款期限",
        "subscriptionStatus": "已退订",
        "cancelTime": "2024-05-15 11:30:22",
    },
    "ORD202405040004": {
        "order_no": "ORD202405040004",
        "productName": "基础会员连续包月",
        "payStatus": "支付失败",
        "payTime": None,
        "payAmount": 19.90,
        "payMethod": "支付宝",
        "refundStatus": "未退款",
        "refundAmount": 0.00,
        "refundTime": None,
        "refundFailReason": None,
        "subscriptionStatus": "已退订",
        "cancelTime": None,
    },
}

_PHONE_ORDERS = {
    "13800138000": {
        "ORD202405030009": {
            "order_no": "ORD202405030003",
            "productName": "高级团队版按月订阅",
            "payStatus": "支付成功",
            "payTime": "2024-05-03 16:45:00",
            "payAmount": 99.00,
            "payMethod": "微信",
            "refundStatus": "未退款",
            "refundAmount": 0.00,
            "refundTime": None,
            "refundFailReason": "已超过7天无理由退款期限",
            "subscriptionStatus": "订阅中",
            "cancelTime": "2024-05-15 11:30:22",
        },
        "ORD202405040008": {
            "order_no": "ORD202405040004",
            "productName": "基础会员连续包月",
            "payStatus": "支付成功",
            "payTime": "2024-05-03 16:45:00",
            "payAmount": 19.90,
            "payMethod": "支付宝",
            "refundStatus": "未退款",
            "refundAmount": 0.00,
            "refundTime": None,
            "refundFailReason": None,
            "subscriptionStatus": "订阅中",
            "cancelTime": None,
        },
    }
}


def _normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(order)
    refund_status = raw.get("refundStatus")
    pay_status = raw.get("payStatus")
    if refund_status == "退款成功":
        status = "refunded"
    elif pay_status == "支付成功":
        status = "paid"
    else:
        status = "unpaid"
    return {
        "ok": True,
        "order_no": raw.get("order_no"),
        "merchant": raw.get("productName"),
        "amount": raw.get("payAmount"),
        "status": status,
        "pay_status": pay_status,
        "pay_time": raw.get("payTime"),
        "pay_method": raw.get("payMethod"),
        "refund_status": refund_status,
        "refund_amount": raw.get("refundAmount"),
        "refund_time": raw.get("refundTime"),
        "refund_fail_reason": raw.get("refundFailReason"),
        "subscription_status": raw.get("subscriptionStatus"),
        "cancel_time": raw.get("cancelTime"),
    }


def _find_order(
    *,
    plugin_name: str,
    order_no: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    def matches_plugin(order: dict[str, Any]) -> bool:
        if order.get("payStatus") != "支付成功":
            return False
        if order.get("refundStatus") != "未退款":
            return False
        if plugin_name == "refund":
            return True
        if plugin_name in {"unsubscribe", "refund_unsubscribe"}:
            return order.get("subscriptionStatus") == "订阅中"
        return False

    if order_no:
        normalized_order_no = order_no.strip().upper()
        order = _ORDERS.get(normalized_order_no)
        if order is None or not matches_plugin(order):
            return {
                "ok": False,
                "error": "order_not_found",
                "order_no": normalized_order_no,
            }
        return _normalize_order(order)

    if phone:
        normalized_phone = "".join(character for character in phone if character.isdigit())
        orders = _PHONE_ORDERS.get(normalized_phone)
        matching_orders = {
            number: order
            for number, order in (orders or {}).items()
            if matches_plugin(order)
        }
        if not matching_orders:
            return {
                "ok": False,
                "error": "order_not_found",
                "phone": normalized_phone,
            }
        _, latest_order = max(
            matching_orders.items(),
            key=lambda item: (str(item[1].get("payTime") or ""), item[0]),
        )
        return _normalize_order(latest_order)

    return {"ok": False, "error": "missing_order_query"}


def query_order(state: dict[str, Any]) -> ActionResult:
    """退款流程中的订单校验能力，也可被后续动作表直接引用。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    call_info = state.get("call_info", {})
    plugin_name = str(state.get("business") or "").strip()

    query_params: dict[str, str] = {}
    if values.get("order_no"):
        query_params["order_no"] = str(values["order_no"])
    elif values.get("backup_phone"):
        query_params["phone"] = str(values["backup_phone"])
    elif call_info.get("caller"):
        query_params["phone"] = str(call_info["caller"])

    # 查询优先级：订单号 > 用户补充手机号 > 本次通话的主叫号码。
    order = _find_order(plugin_name=plugin_name, **query_params)
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
    """组装退款提交参数；实际退款由调用方后续执行。"""

    values = {**state.get("context", {}), **state.get("slots", {})}
    refund_params = {
        "order_no": str(values["order_no"]).upper(),
        "reason": str(values.get("reason") or "用户申请"),
    }
    return action_success(**refund_params, eta="1到3个工作日")
