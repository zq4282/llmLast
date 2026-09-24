from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


def test_other_answers_without_creating_active_flow(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"UNKNOWN","confidence":0.40}',
            '{"decision":"ANSWER","intent":"ask_identity",'
            '"reply":"我是会员业务智能客服。","target_task":null}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "message": "你是谁？",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert result["business"] == "other"
    assert result["handled_by"] == "other"
    assert result["intent"] == "ask_identity"
    assert result["out"] == "CHAT"
    assert result["active_flow_id"] is None
    assert result["flows"] == []
    assert len(model.calls) == 2
    other_prompt = model.calls[1][0]["content"]
    assert "当前状态" not in other_prompt
    assert "当前已查到" not in other_prompt


def test_other_is_overlay_and_keeps_active_flow(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"other","confidence":0.96,"slots":{}}',
            '{"decision":"ANSWER","intent":"ask_capability",'
            '"reply":"我可以协助处理会员业务问题。","target_task":null}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "message": "你能做什么？",
            "history": [],
            "business": "refund",
            "plugin_state": "CONFIRM_REFUND",
            "context": {"order_no": "ORD202405010001", "amount": 299.0},
        }
    )

    assert result["business"] == "refund"
    assert result["handled_by"] == "other"
    assert result["plugin_state"] == "CONFIRM_REFUND"
    assert result["out"] == "CHAT"
    assert "我可以协助处理会员业务问题" in result["reply"]
    assert "退款还在等待确认" not in result["reply"]
    assert result["flows"][0]["status"] == "ACTIVE"


def test_other_can_correct_top_router_and_enter_workflow_same_turn(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"UNKNOWN","confidence":0.52}',
            '{"decision":"ROUTE","intent":null,"reply":null,"target_task":"REFUND"}',
            '{"intent":"refund_request","confidence":0.96,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "message": "订单 ORD202405010001 这笔给我退掉",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert result["business"] == "refund"
    assert result["handled_by"] == "refund"
    assert result["intent"] == "refund_request"
    assert result["plugin_state"] == "CONFIRM_REFUND"
    assert len(model.calls) == 3


def test_other_route_uses_allow_policy_and_preserves_source_flow(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"other","confidence":0.96,"slots":{}}',
            '{"decision":"ROUTE","intent":null,"reply":null,'
            '"target_task":"UNSUBSCRIBE"}',
            '{"intent":"unsubscribe_request","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    result = run_graph(
        {
            "message": "我还要退订",
            "history": [],
            "business": "refund",
            "plugin_state": "CONFIRM_REFUND",
            "context": {"order_no": "ORD202405010001", "amount": 299.0},
        }
    )

    source, target = result["flows"]
    assert source["business"] == "refund"
    assert source["status"] == "SUSPENDED"
    assert source["plugin_state"] == "CONFIRM_REFUND"
    assert target["business"] == "unsubscribe"
    assert target["status"] == "ACTIVE"
    assert result["active_flow_id"] == target["flow_id"]
    assert result["business"] == "unsubscribe"
    assert result["pending_switch"] is None


def test_combined_flow_supersedes_refund_and_reuses_completed_action(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"other","confidence":0.96,"slots":{}}',
            '{"decision":"ROUTE","intent":null,"reply":null,'
            '"target_task":"REFUND_UNSUBSCRIBE"}',
            '{"intent":"refund_unsubscribe_request","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    source_flow = {
        "flow_id": "flow-refund",
        "business": "refund",
        "plugin_state": "ASK_OTHER",
        "status": "ACTIVE",
        "context": {
            "order_no": "ORD202405010001",
            "amount": 299.0,
            "merchant": "专业版年度会员订阅",
        },
        "unrecognized_count": 0,
        "completed_actions": [
            {
                "action": "submit_refund",
                "out": "REFUND",
                "order_no": "ORD202405010001",
                "status": "SUCCESS",
            }
        ],
    }

    result = run_graph(
        {
            "message": "再把订阅也退掉",
            "history": [],
            "business": "refund",
            "plugin_state": "ASK_OTHER",
            "context": source_flow["context"],
            "flows": [source_flow],
            "active_flow_id": "flow-refund",
        }
    )

    source, target = result["flows"]
    assert source["status"] == "SUPERSEDED"
    assert target["business"] == "refund_unsubscribe"
    assert target["status"] == "ACTIVE"
    assert target["context"]["order_no"] == "ORD202405010001"
    assert target["completed_actions"][0]["out"] == "REFUND"
    assert result["plugin_state"] == "CONFIRM_REFUND_UNSUBSCRIBE"


def test_reroute_limit_prevents_other_loop(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"other","confidence":0.96,"slots":{}}',
            '{"decision":"ROUTE","intent":null,"reply":null,"target_task":"REFUND"}',
            '{"intent":"other","confidence":0.92,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "message": "给我处理一下这个退款相关的问题",
            "history": [],
            "business": "refund",
            "plugin_state": "CONFIRM_REFUND",
            "context": {"order_no": "ORD202405010001", "amount": 299.0},
            "unrecognized_count": 0,
        }
    )

    assert len(model.calls) == 3
    assert result["business"] == "refund"
    assert result["plugin_state"] == "CONFIRM_REFUND"
    assert result["unrecognized_count"] == 1
    assert result["out"] == "CHAT"


def test_human_intent_inside_business_is_handled_by_current_plugin(monkeypatch) -> None:
    model = SequenceClient(
        ['{"intent":"human","confidence":0.99,"slots":{}}']
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "message": "给我转人工",
            "history": [],
            "business": "refund",
            "plugin_state": "CONFIRM_REFUND",
            "context": {"order_no": "ORD202405010001", "amount": 299.0},
        }
    )

    assert len(model.calls) == 1
    assert result["business"] == "refund"
    assert result["handled_by"] == "refund"
    assert result["intent"] == "human"
    assert result["out"] == "HUMAN"
    assert result["handoff_reason"] == "USER_REQUESTED"
    assert result["flows"][0]["business"] == "refund"
    assert result["flows"][0]["status"] == "COMPLETED"
