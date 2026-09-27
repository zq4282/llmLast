"""业务查询动作，仅核查和解释，不执行退款、退订或人工跟踪。"""

from typing import Any

from app.engine.action_result import ActionResult, action_failure, action_success
from app.services import order_inquiry
from .scenario_registry import SCENARIO_IDS, load_scenario


def query_order(state: dict[str, Any]) -> ActionResult:
    slots = state.get("slots", {})
    context = state.get("context", {})
    meta = context.get("inquiry_meta", {})
    scenario_id = (state.get("intent") if state.get("intent") in SCENARIO_IDS
                   else slots.get("scenario_id") or meta.get("scenario_id"))
    if scenario_id not in SCENARIO_IDS:
        return action_failure("missing_scenario")
    scenario = load_scenario(scenario_id)
    # 新提供的定位信息优先，不能用旧订单号盖过新的手机号。
    order_no = slots.get("order_no")
    phone = slots.get("backup_phone")
    if not order_no and not phone:
        order_no = meta.get("order_no")
        phone = meta.get("phone") or state.get("call_info", {}).get("caller")
    if not order_no and not phone:
        return action_failure("missing_order_query")
    params = ({"order_no": str(order_no).strip().upper()} if order_no
              else {"phone": "".join(c for c in str(phone) if c.isdigit())})
    try:
        orders = order_inquiry.find_orders(**params)
    except Exception:
        return action_failure("query_unavailable")
    try:
        matches = order_inquiry.matching_orders(orders, slots.get("amount"))
    except ValueError:
        return action_failure("invalid_query")
    except Exception:
        return action_failure("query_unavailable")
    if not matches:
        return action_failure("order_not_found")
    if len(matches) > 1:
        candidates = "；".join(
            f"订单{order['order_no']}，{order['product'] or '产品待核实'}，"
            f"{order['amount'] if order['amount'] is not None else '金额待核实'}元，"
            f"{order['pay_time'] or '扣款时间待核实'}" for order in matches
        )
        # 失败结果只用于本轮展示；下一轮明确提供订单号重新查，不保留未确认事实。
        return action_failure("multiple_orders", candidates=candidates)
    order = matches[0]
    if not order.get("order_no"):
        return action_failure("query_unavailable")
    reply, facts = scenario.explain(order)
    return action_success(
        inquiry_reply=reply,
        inquiry_meta={"scenario_id": scenario_id, "order_no": order["order_no"],
                      "phone": params.get("phone"), "scenario_rules": scenario.prompt},
        verified_order=order,
        scenario_data={"scenario_id": scenario_id, "facts": facts},
    )


__all__ = ["query_order"]
