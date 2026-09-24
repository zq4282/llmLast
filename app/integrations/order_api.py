"""订单系统适配器；当前使用可预测的本地数据模拟远端服务。"""

from copy import deepcopy


_ORDERS = {
    "A1001": {"order_id": "A1001", "amount": 99.0, "status": "paid", "item": "月度套餐"},
    "A1002": {"order_id": "A1002", "amount": 29.9, "status": "refunded", "item": "流量包"},
}


def query_order(order_id: str) -> dict:
    """查询订单；未知订单返回明确的 not_found 状态。"""

    normalized = order_id.strip().upper()
    order = _ORDERS.get(normalized)
    if order is None:
        return {"ok": False, "order_id": normalized, "error": "order_not_found"}
    return {"ok": True, **deepcopy(order)}


def query_current_order() -> dict:
    """模拟按来电账户查询最近一笔可退订单。"""

    return {"ok": True, **deepcopy(_ORDERS["A1001"])}
