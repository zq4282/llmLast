from app.businesses.unsubscribe.handlers import submit_unsubscribe
from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return self.responses.pop(0)


def test_submit_unsubscribe_only_builds_parameters() -> None:
    result = submit_unsubscribe(
        {
            "context": {"order_no": "ord202405010001"},
            "slots": {"reason": "用户确认退订"},
        }
    )

    assert result == {
        "ok": True,
        "order_no": "ORD202405010001",
        "reason": "用户确认退订",
        "effective_time": "立即生效",
    }


def test_unsubscribe_state_machine_runs_across_turns(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"UNSUBSCRIBE","confidence":0.98}',
            '{"intent":"unsubscribe_request","confidence":0.96,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    first = run_graph(
        {
            "session_id": "unsubscribe-test",
            "message": "订单号 ORD202405010001，帮我关闭自动续费",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert first["business"] == "unsubscribe"
    assert first["intent"] == "unsubscribe_request"
    assert first["action"] == "query_order"
    assert first["plugin_state"] == "CONFIRM_UNSUBSCRIBE"
    assert first["out"] == "CHAT"
    assert "是否确认退订并停止后续自动续费" in first["reply"]

    second = run_graph(
        {
            "session_id": "unsubscribe-test",
            "message": "确认退订",
            "history": [
                {
                    "role": "user",
                    "content": "订单号 ORD202405010001，帮我关闭自动续费",
                },
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "context": first["context"],
        }
    )

    assert second["intent"] == "affirm"
    assert second["action"] == "submit_unsubscribe"
    assert second["action_result"] == {
        "ok": True,
        "order_no": "ORD202405010001",
        "reason": "用户申请",
        "effective_time": "立即生效",
    }
    assert second["plugin_state"] == "ASK_OTHER"
    assert second["out"] == "UNSUBSCRIBE"
    assert "已为您退订专业版年度会员订阅" in second["reply"]
    assert "后续将不再自动续费" in second["reply"]


def test_unsubscribe_asks_for_order_info(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"UNSUBSCRIBE","confidence":0.98}',
            '{"intent":"unsubscribe_request","confidence":0.96,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "unsubscribe-missing-order",
            "message": "我要退订",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
        }
    )

    assert result["business"] == "unsubscribe"
    assert result["plugin_state"] == "ASK_ORDER_INFO"
    assert result["action_result"] == {"ok": False, "error": "missing_order_query"}
    assert "可退订订单" in result["reply"]
