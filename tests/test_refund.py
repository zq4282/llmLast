from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


def test_refund_state_machine_runs_across_turns(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND","confidence":0.97}',
            '{"intent":"refund_request","confidence":0.93,'
            '"slots":{"backup_phone":null,"order_id":null}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_id":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    first = run_graph(
        {
            "session_id": "refund-test",
            "message": "为什么扣我钱，赶紧退过来",
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
                {"role": "user", "content": "为什么扣我钱，赶紧退过来"},
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
    assert second["action_result"]["order_id"] == "A1001"
    assert second["plugin_state"] == "ASK_OTHER"
    assert second["intent"] == "affirm"
    assert "已为您提交退款" in second["reply"]
    assert len(model.calls) == 3
    assert sum(call[0]["role"] == "system" for call in model.calls) == 1
