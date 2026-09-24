from app.businesses.human.handlers import record_insult, record_reason
from app.engine.graph import run_graph
from app.engine.llm import intent_llm


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        return self.responses.pop(0)


def test_record_reason_rejects_empty_reason() -> None:
    assert record_reason({"slots": {"reason": "  \n "}}) == {
        "ok": False,
        "error": "missing_reason",
    }


def test_record_insult_increments_program_managed_count() -> None:
    assert record_insult({"context": {"insult_count": 1}}) == {
        "ok": True,
        "sentiment": "insult",
        "insult_count": 2,
    }


def test_human_flow_records_reason_and_handoffs(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"HUMAN","confidence":0.99}',
            '{"intent":"transfer_request","confidence":0.98,"slots":{"reason":null}}',
            '{"intent":"provide_info","confidence":0.96,'
            '"slots":{"reason":"上个月被重复扣费"}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    first = run_graph(
        {
            "session_id": "human-with-reason",
            "message": "我要转人工",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
            "unrecognized_count": 0,
        }
    )
    assert first["business"] == "human"
    assert first["plugin_state"] == "ASK_REASON"
    assert first["out"] == "CHAT"
    assert "遇到的问题" in first["reply"]

    second = run_graph(
        {
            "session_id": "human-with-reason",
            "message": "我上个月扣了两次费，账单不对",
            "history": [
                {"role": "user", "content": "我要转人工"},
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "context": first["context"],
            "unrecognized_count": first["unrecognized_count"],
        }
    )
    assert second["action"] == "record_reason"
    assert second["action_result"] == {"ok": True, "reason": "上个月被重复扣费"}
    assert second["context"]["reason"] == "上个月被重复扣费"
    assert second["plugin_state"] == "END"
    assert second["out"] == "HUMAN"
    assert second["handoff_reason"] == "USER_REQUESTED"
    assert "上个月被重复扣费" in second["reply"]


def test_human_flow_allows_direct_handoff(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"intent":"transfer_request","confidence":0.95,"slots":{"reason":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "human-direct",
            "message": "别问了，直接转吧",
            "history": [],
            "business": "human",
            "plugin_state": "ASK_REASON",
            "context": {},
            "unrecognized_count": 0,
        }
    )

    assert result["action"] == "none"
    assert result["plugin_state"] == "END"
    assert result["out"] == "HUMAN"
    assert result["handoff_reason"] == "USER_REQUESTED"
    assert "正在为您转接人工客服" in result["reply"]


def test_human_flow_can_be_cancelled(monkeypatch) -> None:
    model = SequenceClient(
        ['{"intent":"negate","confidence":0.97,"slots":{"reason":null}}']
    )
    monkeypatch.setattr(intent_llm, "client", model)

    result = run_graph(
        {
            "session_id": "human-cancel",
            "message": "算了，不转了",
            "history": [],
            "business": "human",
            "plugin_state": "ASK_REASON",
            "context": {},
            "unrecognized_count": 0,
        }
    )

    assert result["plugin_state"] == "END"
    assert result["out"] == "CHAT"
    assert "随时告诉我" in result["reply"]


def test_human_flow_handoffs_after_second_insult(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"HUMAN","confidence":0.99}',
            '{"intent":"insult","confidence":0.98,"slots":{"reason":null}}',
            '{"intent":"insult","confidence":0.99,"slots":{"reason":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)

    first = run_graph(
        {
            "session_id": "human-insult",
            "message": "你们是不是傻逼",
            "history": [],
            "business": None,
            "plugin_state": None,
            "context": {},
            "unrecognized_count": 0,
        }
    )

    assert first["business"] == "human"
    assert first["action"] == "record_insult"
    assert first["action_result"] == {
        "ok": True,
        "sentiment": "insult",
        "insult_count": 1,
    }
    assert first["context"]["sentiment"] == "insult"
    assert first["context"]["insult_count"] == 1
    assert first["plugin_state"] == "INSULT_WARNED"
    assert first["out"] == "CHAT"
    assert "请问您遇到的是什么情况" in first["reply"]

    second = run_graph(
        {
            "session_id": "human-insult",
            "message": "垃圾公司，去死吧",
            "history": [
                {"role": "user", "content": "你们是不是傻逼"},
                {"role": "assistant", "content": first["reply"]},
            ],
            "business": first["business"],
            "plugin_state": first["plugin_state"],
            "context": first["context"],
            "unrecognized_count": first["unrecognized_count"],
        }
    )

    assert second["action"] == "record_insult"
    assert second["action_result"] == {
        "ok": True,
        "sentiment": "insult",
        "insult_count": 2,
    }
    assert second["plugin_state"] == "END"
    assert second["out"] == "HUMAN"
    assert second["handoff_reason"] == "USER_REQUESTED"
    assert "马上为您转接人工客服" in second["reply"]
