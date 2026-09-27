"""订单查不到时要能退出，也要允许用户在最后一次核对时提供有效信息。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.engine.graph import run_graph
from app.engine.llm import intent_llm
from app.main import app
from app.session.store import session_store


BUSINESSES = [
    ("refund", "refund_request", "CONFIRM_REFUND"),
    ("unsubscribe", "unsubscribe_request", "CONFIRM_UNSUBSCRIBE"),
    ("refund_unsubscribe", "refund_unsubscribe_request", "CONFIRM_REFUND_UNSUBSCRIBE"),
]


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages, *, temperature=0.0) -> str:
        return self.responses.pop(0)


def intent_response(intent: str, **slots) -> str:
    return json.dumps({"intent": intent, "confidence": 0.98, "slots": slots})


def next_turn(result: dict, message: str) -> dict:
    # 只传下一轮会保存的数据，不能把上一轮的临时意图或动作带过去。
    return {
        key: result[key]
        for key in (
            "business", "plugin_state", "context", "flows", "active_flow_id",
            "pending_switch", "unrecognized_count",
        )
    } | {"message": message, "history": []}


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
@pytest.mark.parametrize("state", ["ASK_ORDER_INFO", "RETRY_ORDER_INFO"])
def test_repeated_request_keeps_lookup_progress_and_can_continue(
    monkeypatch, business, request_intent, confirm_state, state,
) -> None:
    model = SequenceClient([
        *[intent_response(request_intent) for _ in range(3)],
        intent_response("provide_info", order_no="ORD202405010001"),
    ])
    monkeypatch.setattr(intent_llm, "client", model)
    current = {
        "message": "继续办理", "history": [], "business": business,
        "plugin_state": state, "context": {"order_no": "NOT_FOUND"},
        "unrecognized_count": 2,
    }
    flow_id = None
    for _ in range(3):
        result = run_graph(current)
        flow_id = flow_id or result["active_flow_id"]
        assert result["active_flow_id"] == flow_id
        assert result["plugin_state"] == state
        assert result["context"] == {"order_no": "NOT_FOUND"}
        assert result["action"] == "none"
        assert result["out"] == "CHAT"
        assert result["unrecognized_count"] == 0
        assert result["handoff_reason"] is None
        assert "继续为您处理" in result["reply"]
        if state == "RETRY_ORDER_INFO":
            assert "再核对一次" in result["reply"]
        current = next_turn(result, "继续办理")

    continued = run_graph(next_turn(result, "订单号 ORD202405010001"))
    assert continued["active_flow_id"] == flow_id
    assert continued["plugin_state"] == confirm_state
    assert continued["action"] == "query_order"
    assert continued["action_result"]["ok"] is True
    assert model.responses == []


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
@pytest.mark.parametrize("state", ["ASK_ORDER_INFO", "RETRY_ORDER_INFO"])
def test_switch_back_resumes_original_order_information_collection(
    monkeypatch, business, request_intent, confirm_state, state,
) -> None:
    other_business = "unsubscribe" if business == "refund" else "refund"
    route = lambda target: json.dumps({
        "decision": "ROUTE", "target_task": target.upper(),
        "intent": None, "reply": None,
    })
    model = SequenceClient([
        intent_response("other"), route(other_business),
        intent_response(f"{other_business}_request"),
        intent_response("other"), route(business), intent_response(request_intent),
    ])
    monkeypatch.setattr(intent_llm, "client", model)
    original_flow = {
        "flow_id": "original-flow", "business": business,
        "plugin_state": state, "status": "ACTIVE",
        "context": {"order_no": "NOT_FOUND"},
        "unrecognized_count": 2, "completed_actions": [],
    }
    switched = run_graph({
        "message": "改办另一项业务", "history": [],
        "flows": [original_flow], "active_flow_id": "original-flow",
    })
    assert switched["flows"][0]["status"] == "SUSPENDED"
    resumed = run_graph(next_turn(switched, "继续原来的业务"))
    assert resumed["active_flow_id"] == "original-flow"
    assert len(resumed["flows"]) == 2
    assert resumed["business"] == business
    assert resumed["plugin_state"] == state
    assert resumed["context"] == original_flow["context"]
    assert resumed["flows"][0]["status"] == "ACTIVE"
    assert resumed["action"] == "none"
    assert resumed["out"] == "CHAT"
    assert resumed["unrecognized_count"] == 0
    assert resumed["handoff_reason"] is None
    assert "继续为您处理" in resumed["reply"]
    assert model.responses == []


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
def test_api_handoffs_after_order_and_phone_both_fail(
    monkeypatch, business, request_intent, confirm_state,
) -> None:
    model = SequenceClient([
        json.dumps({"task": business.upper(), "confidence": 0.98}),
        intent_response(request_intent),
        intent_response("provide_info", order_no="NOT_FOUND"),
        intent_response("provide_info", backup_phone="13900000000"),
    ])
    monkeypatch.setattr(intent_llm, "client", model)
    session_store.clear()
    session_id = f"lookup-retry-{business}"

    with TestClient(app) as client:
        first = client.post("/api/chat", json={
            "sessionId": session_id,
            "currentUserText": "我要办理",
            "callInfo": {"caller": "13911111111"},
        })
        assert first.status_code == 200
        assert first.json()["out"] == "CHAT"
        assert session_store.get(session_id).plugin_state == "ASK_ORDER_INFO"

        second = client.post("/api/chat", json={
            "sessionId": session_id, "currentUserText": "订单号 NOT_FOUND",
        })
        assert second.status_code == 200
        assert second.json()["out"] == "CHAT"
        assert "再核对一次" in second.json()["reply"]
        stored = session_store.get(session_id)
        assert stored.plugin_state == "RETRY_ORDER_INFO"
        assert stored.context == {}

        third = client.post("/api/chat", json={
            "sessionId": session_id, "currentUserText": "手机号 13900000000",
        })
        assert third.status_code == 200
        assert third.json()["out"] == "HUMAN"
        assert "转接人工" in third.json()["reply"]
        assert third.json()["data"]["handoff_reason"] == "ACTION_FAILED"
        stored = session_store.get(session_id)
        assert stored.active_flow_id is None
        assert stored.business is None
        assert stored.flows[-1]["status"] == "COMPLETED"
        assert stored.flows[-1]["completed_actions"] == []
        assert model.responses == []


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
def test_last_order_lookup_can_recover_using_phone(
    monkeypatch, business, request_intent, confirm_state,
) -> None:
    monkeypatch.setattr(intent_llm, "client", SequenceClient([
        intent_response("provide_info", order_no="NOT_FOUND"),
        intent_response("provide_info", backup_phone="13800138000"),
    ]))
    failed = run_graph({
        "message": "订单号 NOT_FOUND", "history": [],
        "business": business, "plugin_state": "ASK_ORDER_INFO", "context": {},
    })
    assert failed["plugin_state"] == "RETRY_ORDER_INFO"
    assert failed["context"] == {}

    recovered = run_graph(next_turn(failed, "那用手机号 13800138000 查"))
    assert recovered["plugin_state"] == confirm_state
    assert recovered["out"] == "CHAT"
    assert recovered["action_result"]["ok"] is True
    assert recovered["context"]["order_no"] == "ORD202405040008"
    assert recovered["flows"][-1]["status"] == "ACTIVE"
    assert recovered["handoff_reason"] is None


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
@pytest.mark.parametrize("state", ["ASK_ORDER_INFO", "RETRY_ORDER_INFO"])
@pytest.mark.parametrize("intent,output", [
    ("unable_to_provide", "HUMAN"), ("negate", "END"), ("end", "END"),
])
def test_user_can_leave_order_information_collection(
    monkeypatch, business, request_intent, confirm_state, state, intent, output,
) -> None:
    monkeypatch.setattr(intent_llm, "client", SequenceClient([intent_response(intent)]))
    result = run_graph({
        "message": "没有其他订单信息了", "history": [],
        "business": business, "plugin_state": state, "context": {},
    })
    assert result["out"] == output
    assert result["plugin_state"] == "END"
    assert result["action"] == "none"
    assert result["active_flow_id"] is None
    assert result["flows"][-1]["status"] == "COMPLETED"


@pytest.mark.parametrize("business,request_intent,confirm_state", BUSINESSES)
def test_last_lookup_missing_information_also_handoffs(
    monkeypatch, business, request_intent, confirm_state,
) -> None:
    monkeypatch.setattr(intent_llm, "client", SequenceClient([
        intent_response("provide_info"),
    ]))
    result = run_graph({
        "message": "信息就在这里", "history": [],
        "business": business, "plugin_state": "RETRY_ORDER_INFO", "context": {},
    })
    assert result["action_result"]["error"] == "missing_order_query"
    assert result["out"] == "HUMAN"
    assert result["handoff_reason"] == "ACTION_FAILED"
    assert result["active_flow_id"] is None
