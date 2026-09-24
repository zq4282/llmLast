import json

import pytest

from app.businesses.refund_unsubscribe.handlers import submit_refund_unsubscribe
from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return self.responses.pop(0)


@pytest.mark.parametrize("state", ["ASK_ORDER_INFO", "RETRY_ORDER_INFO"])
@pytest.mark.parametrize("business,message", [
    ("refund", "我要退款"), ("unsubscribe", "我要退订"),
])
@pytest.mark.parametrize("has_suspended_flow", [False, True])
def test_combined_flow_can_switch_to_current_single_request(
    monkeypatch, state, business, message, has_suspended_flow,
) -> None:
    model = SequenceClient([
        json.dumps({"intent": "other", "confidence": 0.99, "slots": {}}),
        json.dumps({"decision": "ROUTE", "target_task": business.upper(),
                    "intent": None, "reply": None}),
        json.dumps({"intent": f"{business}_request", "confidence": 0.99, "slots": {}}),
    ])
    monkeypatch.setattr(intent_llm, "client", model)
    combined = {
        "flow_id": "combined", "business": "refund_unsubscribe",
        "plugin_state": state, "status": "ACTIVE", "context": {},
        "unrecognized_count": 0, "completed_actions": [],
    }
    flows = [combined]
    if has_suspended_flow:
        flows.insert(0, {
            **combined, "flow_id": "single", "business": business,
            "status": "SUSPENDED",
        })
    result = run_graph({
        "message": message,
        "history": [
            {"role": "user", "content": "我要退款退订"},
            {"role": "assistant", "content": "请提供手机号或订单号以办理退款并退订。"},
        ],
        "flows": flows, "active_flow_id": "combined",
    })
    assert result["business"] == business
    assert result["intent"] == f"{business}_request"
    assert result["plugin_state"] == (state if has_suspended_flow else "ASK_ORDER_INFO")
    assert result["out"] == "CHAT"
    assert result["unrecognized_count"] == 0
    assert "退款并退订" not in result["reply"]
    assert next(flow for flow in result["flows"] if flow["flow_id"] == "combined")["status"] == "SUSPENDED"
    if has_suspended_flow:
        assert result["active_flow_id"] == "single"

    # 再切回组合业务，应恢复组合业务原来的核对进度。
    model.responses.extend([
        json.dumps({"intent": "other", "confidence": 0.99, "slots": {}}),
        json.dumps({"decision": "ROUTE", "target_task": "REFUND_UNSUBSCRIBE",
                    "intent": None, "reply": None}),
        json.dumps({"intent": "refund_unsubscribe_request", "confidence": 0.99, "slots": {}}),
    ])
    resumed = run_graph({
        **{key: result[key] for key in (
            "business", "plugin_state", "context", "flows", "active_flow_id",
            "pending_switch", "unrecognized_count",
        )},
        "message": "我要退款退订", "history": [],
    })
    assert resumed["active_flow_id"] == "combined"
    assert resumed["plugin_state"] == state
    assert resumed["out"] == "CHAT"
    assert resumed["unrecognized_count"] == 0
    assert "继续为您处理退款并退订" in resumed["reply"]
    assert model.responses == []


def test_submit_refund_unsubscribe_builds_combined_parameters() -> None:
    result = submit_refund_unsubscribe(
        {
            "context": {"order_no": "ord202405010001"},
            "slots": {"reason": "用户确认退款并退订"},
        }
    )

    assert result == {
        "ok": True,
        "order_no": "ORD202405010001",
        "reason": "用户确认退款并退订",
        "eta": "1到3个工作日",
        "effective_time": "立即生效",
        "performed_actions": ["refund", "unsubscribe"],
        "skipped_actions": [],
        "refund_result": "退款已提交，预计1到3个工作日到账",
        "unsubscribe_result": "退订立即生效，后续不再自动续费",
    }


def test_combined_action_skips_refund_that_already_succeeded() -> None:
    result = submit_refund_unsubscribe(
        {
            "context": {"order_no": "ORD202405010001"},
            "slots": {},
            "completed_actions": [
                {
                    "action": "submit_refund",
                    "out": "REFUND",
                    "order_no": "ORD202405010001",
                    "status": "SUCCESS",
                }
            ],
        }
    )

    assert result["performed_actions"] == ["unsubscribe"]
    assert result["skipped_actions"] == ["refund"]
    assert "不重复退款" in result["refund_result"]


def test_refund_unsubscribe_state_machine_runs_across_turns(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND_UNSUBSCRIBE","confidence":0.98}',
            '{"intent":"refund_unsubscribe_request","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
            '{"intent":"affirm","confidence":0.97,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    first = run_graph(
        {
            "session_id": "refund-unsubscribe-test",
            "message": "订单号 ORD202405010001，这次扣的钱退给我，下个月也别扣了",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert first["business"] == "refund_unsubscribe"
    assert first["intent"] == "refund_unsubscribe_request"
    assert first["action"] == "query_order"
    assert first["plugin_state"] == "CONFIRM_REFUND_UNSUBSCRIBE"
    assert first["out"] == "CHAT"
    assert "是否确认退款并退订" in first["reply"]
    assert "停止后续自动续费" in first["reply"]

    second = run_graph(
        {
            "session_id": "refund-unsubscribe-test",
            "message": "对，都办了",
            "history": [
                {
                    "role": "user",
                    "content": "订单号 ORD202405010001，这次扣的钱退给我，下个月也别扣了",
                },
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "context": first["context"],
        }
    )

    assert second["action"] == "submit_refund_unsubscribe"
    assert second["action_result"] == {
        "ok": True,
        "order_no": "ORD202405010001",
        "reason": "用户申请",
        "eta": "1到3个工作日",
        "effective_time": "立即生效",
        "performed_actions": ["refund", "unsubscribe"],
        "skipped_actions": [],
        "refund_result": "退款已提交，预计1到3个工作日到账",
        "unsubscribe_result": "退订立即生效，后续不再自动续费",
    }
    assert second["plugin_state"] == "ASK_OTHER"
    assert second["out"] == "REFUND_UNSUBSCRIBE"
    assert "退款已提交" in second["reply"]
    assert "后续不再自动续费" in second["reply"]
    assert "预计1到3个工作日到账" in second["reply"]


def test_refund_unsubscribe_asks_for_order_info(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND_UNSUBSCRIBE","confidence":0.98}',
            '{"intent":"refund_unsubscribe_request","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "refund-unsubscribe-missing-order",
            "message": "不要扣钱了，下个月也不要再扣了",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert result["business"] == "refund_unsubscribe"
    assert result["plugin_state"] == "ASK_ORDER_INFO"
    assert result["action_result"] == {"ok": False, "error": "missing_order_query"}
    assert "可退款并退订的订单" in result["reply"]
