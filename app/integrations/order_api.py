"""订单系统适配器；当前使用可预测的本地数据模拟远端服务。"""

from copy import deepcopy
from typing import Any

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
    "17600184282": {
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
        }}
}


def _normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    """把模拟接口的字段转换成业务层统一使用的字段。"""

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


def _query_by_order_no(order_no: str) -> dict[str, Any]:
    normalized = order_no.strip().upper()
    order = _ORDERS.get(normalized)
    if order is None:
        return {"ok": False, "error": "order_not_found", "order_no": normalized}
    return _normalize_order(order)


def _query_latest_by_phone(phone: str) -> dict[str, Any]:
    normalized = "".join(character for character in phone if character.isdigit())
    orders = _PHONE_ORDERS.get(normalized)
    if not orders:
        return {"ok": False, "error": "order_not_found", "phone": normalized}
    _, latest_order = max(
        orders.items(),
        key=lambda item: (str(item[1].get("payTime") or ""), item[0]),
    )
    return _normalize_order(latest_order)


def query_order(
    *,
    order_no: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """按动态传入的订单号或手机号查询；同时传入时优先使用订单号。"""

    if order_no:
        return _query_by_order_no(order_no)
    if phone:
        return _query_latest_by_phone(phone)
    return {"ok": False, "error": "missing_order_query"}
