"""核查查询：保留全部订单状态，不改变办理查询的筛选条件。

find_orders 是业务接口接入点；当前读取项目已有的演示订单。
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services import order_lookup


def find_orders(*, order_no: str | None = None, phone: str | None = None) -> list[dict[str, Any]]:
    if order_no:
        order = order_lookup._ORDERS.get(order_no)
        return [deepcopy(order)] if order else []
    return deepcopy(list(order_lookup._PHONE_ORDERS.get(phone or "", {}).values()))


def normalize_order(raw: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "order_no": "order_no", "product": "productName", "amount": "payAmount",
        "pay_status": "payStatus", "pay_time": "payTime", "pay_method": "payMethod",
        "subscription_status": "subscriptionStatus", "subscription_time": "subscriptionTime",
        "subscription_channel": "subscriptionChannel", "scan_relation": "scanRelation",
        "refund_status": "refundStatus", "refund_channel": "refundChannel",
        "refund_request_time": "refundRequestTime", "refund_time": "refundTime",
        "refund_fail_reason": "refundFailReason",
    }
    return {name: raw.get(source) for name, source in fields.items()}


def matching_orders(orders: list[dict[str, Any]], amount: Any = None) -> list[dict[str, Any]]:
    """金额仅缩小候选范围，不自动选最新记录。"""
    normalized = [normalize_order(raw) for raw in orders]
    if amount is None:
        return normalized
    try:
        target = Decimal(str(amount))
        if not target.is_finite() or target < 0:
            raise ValueError("invalid_amount")
        return [order for order in normalized if order["amount"] is not None
                and Decimal(str(order["amount"])) == target]
    except InvalidOperation as exc:
        raise ValueError("invalid_amount") from exc
