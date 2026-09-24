from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


def _next_turn(previous: dict, message: str) -> dict:
    return {
        "session_id": "recovery-test",
        "message": message,
        "history": [],
        "business": previous["business"],
        "plugin_state": previous.get("plugin_state"),
        "route_task": previous.get("route_task"),
        "route_confidence": previous.get("route_confidence"),
        "route_locked": previous.get("route_locked", False),
        "context": previous.get("context", {}),
        "unrecognized_count": previous.get("unrecognized_count", 0),
        "conversation_status": previous.get("conversation_status", "BOT"),
        "handoff_reason": previous.get("handoff_reason"),
    }


def test_three_unknown_turns_escalate_and_keep_business_state(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"unknown","confidence":0.21,"slots":{}}',
            '{"intent":"unknown","confidence":0.18,"slots":{}}',
            '{"intent":"unknown","confidence":0.12,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    current = {
        "business": "refund",
        "plugin_state": "CONFIRM_REFUND",
        "route_task": "REFUND",
        "route_confidence": 0.97,
        "route_locked": True,
        "context": {"order_no": "ORD202405010001", "amount": 299.0},
        "unrecognized_count": 0,
        "conversation_status": "BOT",
    }

    first = run_graph(_next_turn(current, "adfadfadf"))
    second = run_graph(_next_turn(first, "adfadfadfasdfa"))
    third = run_graph(_next_turn(second, "还是听不懂的内容"))

    assert first["unrecognized_count"] == 1
    assert first["out"] == "CHAT"
    assert "是否申请退回这笔299.0元扣款" in first["reply"]
    assert second["unrecognized_count"] == 2
    assert "转人工" in second["reply"]
    assert third["unrecognized_count"] == 3
    assert third["out"] == "HUMAN"
    assert third["conversation_status"] == "HANDOFF_PENDING"
    assert third["handoff_reason"] == "CONSECUTIVE_UNRECOGNIZED"
    assert third["plugin_state"] == "CONFIRM_REFUND"
    assert third["context"]["order_no"] == "ORD202405010001"

    waiting = run_graph(_next_turn(third, "喂"))
    assert waiting["out"] == "HUMAN"
    assert waiting["conversation_status"] == "HANDOFF_PENDING"
    assert len(model.calls) == 3


def test_recognized_intent_resets_unknown_count(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"unknown","confidence":0.21,"slots":{}}',
            '{"intent":"unknown","confidence":0.18,"slots":{}}',
            '{"intent":"affirm","confidence":0.98,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    current = {
        "business": "refund",
        "plugin_state": "CONFIRM_REFUND",
        "route_task": "REFUND",
        "route_confidence": 0.97,
        "route_locked": True,
        "context": {"order_no": "ORD202405010001", "amount": 299.0},
        "unrecognized_count": 0,
        "conversation_status": "BOT",
    }

    first = run_graph(_next_turn(current, "听不清"))
    second = run_graph(_next_turn(first, "还是乱码"))
    recovered = run_graph(_next_turn(second, "对，退款"))

    assert recovered["intent"] == "affirm"
    assert recovered["unrecognized_count"] == 0
    assert recovered["action"] == "submit_refund"
    assert recovered["out"] == "REFUND"


def test_unknown_during_refund_confirmation_uses_contextual_replies(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"unknown","confidence":0.10,"slots":{}}',
            '{"intent":"unknown","confidence":0.08,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    current = {
        "business": "refund",
        "plugin_state": "CONFIRM_REFUND",
        "route_task": "REFUND",
        "route_confidence": 0.97,
        "route_locked": True,
        "context": {"order_no": "ORD202405010001", "amount": 19.9},
        "unrecognized_count": 0,
        "conversation_status": "BOT",
    }

    first = run_graph(_next_turn(current, "asdfasdf"))
    second = run_graph(_next_turn(first, "qwer123"))

    assert first["plugin_state"] == "CONFIRM_REFUND"
    assert first["unrecognized_count"] == 1
    assert "19.9元" in first["reply"]
    assert "退款”或“不退款" in first["reply"]
    assert second["unrecognized_count"] == 2
    assert "退款”“不退款”或“转人工" in second["reply"]


def test_unsupported_intent_does_not_repeat_forever(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"other","confidence":0.90,"slots":{}}',
            '{"intent":"other","confidence":0.90,"slots":{}}',
            '{"intent":"other","confidence":0.90,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    current = {
        "business": "refund",
        "plugin_state": "CONFIRM_REFUND",
        "route_task": "REFUND",
        "route_confidence": 0.97,
        "route_locked": True,
        "context": {"order_no": "ORD202405010001", "amount": 19.9},
        "unrecognized_count": 0,
        "conversation_status": "BOT",
    }

    first = run_graph(_next_turn(current, "asdfasdf"))
    second = run_graph(_next_turn(first, "还是不相关的内容"))
    third = run_graph(_next_turn(second, "继续不回答"))

    assert first["unrecognized_count"] == 1
    assert "当前正在确认这笔19.9元扣款" in first["reply"]
    assert second["unrecognized_count"] == 2
    assert first["reply"] != second["reply"]
    assert third["unrecognized_count"] == 3
    assert third["out"] == "HUMAN"
    assert third["conversation_status"] == "HANDOFF_PENDING"
    assert third["handoff_reason"] == "CONSECUTIVE_UNSUPPORTED"


def test_router_unknown_uses_recovery_without_loading_plugin(monkeypatch) -> None:
    model = SequenceClient(['{"task":"UNKNOWN","confidence":0.15}'])
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "router-unknown",
            "message": "adfadfadf",
            "history": [],
            "business": None,
            "plugin_state": None,
            "route_task": None,
            "route_locked": False,
            "context": {},
            "unrecognized_count": 0,
            "conversation_status": "BOT",
        }
    )

    assert result["business"] == "system"
    assert result["intent"] == "unknown"
    assert result["unrecognized_count"] == 1
    assert result["out"] == "CHAT"


def test_router_human_request_transfers_immediately(monkeypatch) -> None:
    model = SequenceClient(['{"task":"HUMAN","confidence":0.99}'])
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "router-human",
            "message": "转人工",
            "history": [],
            "business": None,
            "plugin_state": None,
            "route_task": None,
            "route_locked": False,
            "context": {},
            "unrecognized_count": 0,
            "conversation_status": "BOT",
        }
    )

    assert result["out"] == "HUMAN"
    assert result["conversation_status"] == "HANDOFF_PENDING"
    assert result["handoff_reason"] == "USER_REQUESTED"
    assert result["unrecognized_count"] == 0
