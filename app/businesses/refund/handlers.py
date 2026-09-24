"""退款业务处理函数。"""

from typing import Any

from app.integrations import order_api, refund_api


def query_order(state: dict[str, Any]) -> dict[str, Any]:
    """退款流程中的订单校验能力，也可被后续动作表直接引用。"""

    return order_api.query_order(state["slots"]["order_id"].upper())


def submit_refund(state: dict[str, Any]) -> dict[str, Any]:
    slots = state["slots"]
    return refund_api.submit_refund(
        slots["order_id"].upper(),
        slots.get("reason", "用户申请"),
    )


def query_refund(state: dict[str, Any]) -> dict[str, Any]:
    return refund_api.query_refund(state["slots"]["order_id"].upper())
