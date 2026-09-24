from app.engine.graph import run_graph
from app.engine.llm import intent_llm
from app.integrations import refund_api


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


def test_refund_api_uses_order_no_everywhere() -> None:
    submitted = refund_api.submit_refund("ORD202405030003")
    queried = refund_api.query_refund("ORD202405030003")

    assert submitted["order_no"] == "ORD202405030003"
    assert queried["order_no"] == "ORD202405030003"
    assert "order_id" not in submitted
    assert "order_id" not in queried


def test_refund_state_machine_runs_across_turns(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND","confidence":0.97}',
            '{"intent":"refund_request","confidence":0.93,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    first = run_graph(
        {
            "session_id": "refund-test",
            "message": "订单号 ORD202405010001，赶紧退过来",
            "history": [],
            "business": None,
            "plugin_state": None,
            "route_task": None,
            "route_locked": False,
            "context": {},
        }
    )
    assert first["business"] == "refund"
    assert first["plugin_state"] == "CONFIRM_REFUND"
    assert first["intent"] == "refund_request"
    assert first["action"] == "query_order"
    assert "是否需要为您申请退款" in first["reply"]

    second = run_graph(
        {
            "session_id": "refund-test",
            "message": "就是那笔",
            "history": [
                {"role": "user", "content": "订单号 ORD202405010001，赶紧退过来"},
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "route_task": first["route_task"],
            "route_confidence": first["route_confidence"],
            "route_locked": first["route_locked"],
            "context": first["context"],
        }
    )
    assert second["action_result"]["ok"] is True
    assert second["action_result"]["order_no"] == "ORD202405010001"
    assert second["plugin_state"] == "ASK_OTHER"
    assert second["intent"] == "affirm"
    assert "已为您提交退款" in second["reply"]
    assert len(model.calls) == 3
    assert sum(call[0]["role"] == "system" for call in model.calls) == 1


def test_refund_asks_for_order_info_and_continues_with_phone(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND","confidence":0.97}',
            '{"intent":"refund_request","confidence":0.93,'
            '"slots":{"backup_phone":null,"order_no":null}}',
            '{"intent":"provide_info","confidence":0.98,'
            '"slots":{"backup_phone":"17600184282","order_no":null}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    first = run_graph(
        {
            "session_id": "refund-missing-order",
            "message": "我要退款",
            "history": [],
            "business": None,
            "plugin_state": None,
            "route_task": None,
            "route_locked": False,
            "context": {},
        }
    )
    assert first["plugin_state"] == "ASK_ORDER_INFO"
    assert first["out"] == "CHAT"
    assert first["action_result"] == {"ok": False, "error": "missing_order_query"}
    assert "手机号或订单号" in first["reply"]

    second = run_graph(
        {
            "session_id": "refund-missing-order",
            "message": "手机号是 17600184282",
            "history": [
                {"role": "user", "content": "我要退款"},
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "route_task": first["route_task"],
            "route_confidence": first["route_confidence"],
            "route_locked": first["route_locked"],
            "context": first["context"],
        }
    )
    assert second["action"] == "query_order"
    assert second["action_result"]["order_no"] == "ORD202405040004"
    assert second["plugin_state"] == "CONFIRM_REFUND"
    assert "是否需要为您申请退款" in second["reply"]

    third = run_graph(
        {
            "session_id": "refund-missing-order",
            "message": "确认退款",
            "history": [
                {"role": "user", "content": "我要退款"},
                {"role": "assistant", "content": first["reply"]},
                {"role": "user", "content": "手机号是 17600184282"},
                {"role": "assistant", "content": second["reply"]},
            ],
            "business": second["business"],
            "plugin_state": second["plugin_state"],
            "route_task": second["route_task"],
            "route_confidence": second["route_confidence"],
            "route_locked": second["route_locked"],
            "context": second["context"],
        }
    )
    assert third["action"] == "submit_refund"
    assert third["action_result"]["ok"] is True
    assert third["plugin_state"] == "ASK_OTHER"
    assert third["out"] == "REFUND"


def test_refund_keeps_asking_when_supplied_order_is_not_found(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"provide_info","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":"NOT_FOUND"}}',
            '{"intent":"provide_info","confidence":0.98,'
            '"slots":{"backup_phone":"17600184282","order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "refund-invalid-order",
            "message": "订单号是 NOT_FOUND",
            "history": [],
            "business": "refund",
            "plugin_state": "ASK_ORDER_INFO",
            "route_task": "REFUND",
            "route_confidence": 0.97,
            "route_locked": True,
            "context": {},
        }
    )
    assert result["plugin_state"] == "ASK_ORDER_INFO"
    assert result["out"] == "CHAT"
    assert result["action_result"]["ok"] is False
    assert result["action_result"]["error"] == "order_not_found"
    assert "仍未查到订单" in result["reply"]
    assert "order_no" not in result["context"]

    recovered = run_graph(
        {
            "session_id": "refund-invalid-order",
            "message": "那用手机号 17600184282 查",
            "history": [
                {"role": "user", "content": "订单号是 NOT_FOUND"},
                {"role": "assistant", "content": result["reply"]},
            ],
            "business": result["business"],
            "plugin_state": result["plugin_state"],
            "route_task": result["route_task"],
            "route_confidence": result["route_confidence"],
            "route_locked": result["route_locked"],
            "context": result["context"],
        }
    )
    assert recovered["plugin_state"] == "CONFIRM_REFUND"
    assert recovered["action_result"]["order_no"] == "ORD202405040004"


def test_refund_continues_when_user_supplies_order_number(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"provide_info","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "refund-order-number",
            "message": "订单号是 ORD202405010001",
            "history": [],
            "business": "refund",
            "plugin_state": "ASK_ORDER_INFO",
            "route_task": "REFUND",
            "route_confidence": 0.97,
            "route_locked": True,
            "context": {},
        }
    )
    assert result["action_result"]["order_no"] == "ORD202405010001"
    assert result["plugin_state"] == "CONFIRM_REFUND"
    assert "是否需要为您申请退款" in result["reply"]


def test_refund_end_state_returns_end_output(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"end","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":null}}',
            '{"intent":"other","confidence":0.90,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    ended = run_graph(
        {
            "session_id": "refund-end",
            "message": "没有其他问题了",
            "history": [],
            "business": "refund",
            "plugin_state": "ASK_OTHER",
            "route_task": "REFUND",
            "route_confidence": 0.97,
            "route_locked": True,
            "context": {"order_no": "ORD202405010001"},
        }
    )
    assert ended["plugin_state"] == "END"
    assert ended["out"] == "END"
    assert "感谢您的来电" in ended["reply"]

    repeated = run_graph(
        {
            "session_id": "refund-end",
            "message": "喂",
            "history": [],
            "business": "refund",
            "plugin_state": ended["plugin_state"],
            "route_task": "REFUND",
            "route_confidence": 0.97,
            "route_locked": True,
            "context": ended["context"],
        }
    )
    assert repeated["plugin_state"] == "END"
    assert repeated["out"] == "END"
    assert repeated["action"] == "none"
