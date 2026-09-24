from fastapi.testclient import TestClient

from app.engine.llm import intent_llm
from app.main import app
from app.session.store import session_store


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


def test_chat_api_and_session_follow_up(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND","confidence":0.97}',
            '{"intent":"refund_request","confidence":0.93,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_no":null}}',
            '{"intent":"end","confidence":0.98,'
            '"slots":{"backup_phone":null,"order_no":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    session_store.clear()
    with TestClient(app) as client:
        first = client.post(
            "/api/chat",
            json={
                "sessionId": "api-test",
                "tenantId": 1002,
                "callInfo": {
                    "caller": "13800138000",
                    "callee": "10000",
                    "callStartTime": "2026-09-22 10:00:00",
                },
                "systemPrompt": "你是会员业务客服，请识别用户意图并生成回复话术",
                "historyContext": [],
                "currentUserText": "订单号 ORD202405010001，赶紧退过来",
                "config": {"maxReplyLen": 60, "temperature": 0.1},
            },
        )
        assert first.status_code == 200
        assert first.json()["business"] == "refund"
        assert first.json()["intent"] == "refund_request"
        assert first.json()["out"] == "CHAT"
        assert "是否需要为您申请退款" in first.json()["reply"]
        stored_after_first = session_store.get("api-test")
        assert stored_after_first.call_info == {
            "caller": "13800138000",
            "callee": "10000",
            "call_start_time": "2026-09-22 10:00:00",
        }
        assert "caller" not in stored_after_first.context

        second = client.post("/api/chat", json={"sessionId": "api-test", "currentUserText": "就是那笔"})
        assert second.status_code == 200
        body = second.json()
        assert body["business"] == "refund"
        assert body["intent"] == "affirm"
        assert body["action"] == "submit_refund"
        assert body["out"] == "REFUND"
        assert body["data"]["order_no"] == "ORD202405010001"
        end_response = client.post("/api/chat", json={"sessionId": "api-test", "currentUserText": "没有了"})
        assert end_response.status_code == 200
        assert end_response.json()["out"] == "END"
        ended_session = session_store.get("api-test")
        assert ended_session.business is None
        assert ended_session.call_info == {}
        assert ended_session.context == {}
        assert ended_session.history == []

        assert len(model.calls) == 4
        assert sum(call[0]["role"] == "system" for call in model.calls) == 1


def test_blank_message_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"currentUserText": "   "})
    assert response.status_code == 422


def test_api_clears_session_after_handoff(monkeypatch) -> None:
    model = SequenceClient(
        [
            '{"task":"REFUND","confidence":0.97}',
            '{"intent":"refund_request","confidence":0.93,'
            '"slots":{"backup_phone":null,"order_no":"ORD202405010001"}}',
            '{"intent":"unknown","confidence":0.20,"slots":{}}',
            '{"intent":"unknown","confidence":0.16,"slots":{}}',
            '{"intent":"unknown","confidence":0.11,"slots":{}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    session_store.clear()

    with TestClient(app) as client:
        first = client.post(
            "/api/chat",
            json={
                "sessionId": "api-recovery",
                "currentUserText": "订单号 ORD202405010001，我要退款",
            },
        )
        assert first.status_code == 200

        first_unknown = client.post(
            "/api/chat",
            json={"sessionId": "api-recovery", "currentUserText": "adfadfadf"},
        )
        second_unknown = client.post(
            "/api/chat",
            json={"sessionId": "api-recovery", "currentUserText": "adfadfadfasdfa"},
        )
        third_unknown = client.post(
            "/api/chat",
            json={"sessionId": "api-recovery", "currentUserText": "还是乱码"},
        )

        assert first_unknown.json()["out"] == "CHAT"
        assert second_unknown.json()["out"] == "CHAT"
        assert third_unknown.json()["out"] == "HUMAN"
        assert third_unknown.json()["data"] == {
            "unrecognized_count": 3,
            "handoff_reason": "CONSECUTIVE_UNRECOGNIZED",
        }

        stored = session_store.get("api-recovery")
        assert stored.business is None
        assert stored.plugin_state is None
        assert stored.context == {}
        assert stored.history == []
        assert stored.unrecognized_count == 0
        assert len(model.calls) == 5


def test_openapi_uses_new_camel_case_request_fields() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    request_schema = schema["components"]["schemas"]["ChatRequest"]
    assert request_schema["required"] == ["currentUserText"]
    assert set(request_schema["properties"]) == {
        "sessionId",
        "tenantId",
        "callInfo",
        "systemPrompt",
        "historyContext",
        "currentUserText",
        "config",
    }


def test_frontend_is_served() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        stylesheet = client.get("/static/styles.css")
        script = client.get("/static/app.js")
        tokens = client.get("/tokens.css")

    assert page.status_code == 200
    assert "对话引擎测试台" in page.text
    assert "主叫号码" in page.text
    assert stylesheet.status_code == 200
    assert "macrostructure: Workbench" in stylesheet.text
    assert script.status_code == 200
    assert 'fetch("/api/chat"' in script.text
    assert "currentUserText" in script.text
    assert "crypto?.randomUUID" in script.text
    assert "return window.crypto.randomUUID()" in script.text
    assert "elements.session.value = createSessionId()" in script.text
    assert "CALL_001" not in page.text
    assert tokens.status_code == 200
    assert "--color-accent" in tokens.text
