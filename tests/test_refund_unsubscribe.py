from app.businesses.refund_unsubscribe.handlers import submit_refund_unsubscribe
from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return self.responses.pop(0)


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
    }


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
    }
    assert second["plugin_state"] == "ASK_OTHER"
    assert second["out"] == "REFUND_UNSUBSCRIBE"
    assert "已为您提交退款并退订" in second["reply"]
    assert "后续不再自动续费" in second["reply"]
    assert "退款预计1到3个工作日到账" in second["reply"]


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
