"""退款系统适配器。"""

from threading import RLock
from uuid import uuid4

from app.integrations.order_api import query_order


_REFUNDS: dict[str, dict] = {}
_LOCK = RLock()


def submit_refund(order_no: str, reason: str = "用户申请") -> dict:
    order = query_order(order_no=order_no)
    if not order["ok"]:
        return order
    if order["status"] == "refunded":
        return {"ok": False, "error": "already_refunded", "order_no": order_no}

    with _LOCK:
        existing = next((item for item in _REFUNDS.values() if item["order_no"] == order_no), None)
        if existing:
            return {"ok": True, **existing, "duplicate": True}
        refund = {
            "refund_id": f"R{uuid4().hex[:8].upper()}",
            "order_no": order_no,
            "amount": order["amount"],
            "reason": reason,
            "status": "processing",
        }
        _REFUNDS[refund["refund_id"]] = refund
        return {"ok": True, **refund}


def query_refund(order_no: str) -> dict:
    with _LOCK:
        refund = next((item for item in _REFUNDS.values() if item["order_no"] == order_no), None)
    if refund:
        return {"ok": True, **refund}
    return {"ok": False, "error": "refund_not_found", "order_no": order_no}
