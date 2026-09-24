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
            '"slots":{"backup_phone":null,"order_id":null}}',
            '{"intent":"affirm","confidence":0.95,'
            '"slots":{"backup_phone":null,"order_id":null}}',
        ]
    )
    monkeypatch.setattr(intent_llm, "client", model)
    session_store.clear()
    with TestClient(app) as client:
        first = client.post(
            "/api/chat",
            json={"session_id": "api-test", "message": "为什么扣我钱，赶紧退过来"},
        )
        assert first.status_code == 200
        assert first.json()["business"] == "refund"
        assert first.json()["intent"] == "refund_request"
        assert first.json()["out"] == "CHAT"
        assert "是否需要为您申请退款" in first.json()["reply"]

        second = client.post("/api/chat", json={"session_id": "api-test", "message": "就是那笔"})
        assert second.status_code == 200
        body = second.json()
        assert body["business"] == "refund"
        assert body["intent"] == "affirm"
        assert body["action"] == "submit_refund"
        assert body["out"] == "REFUND"
        assert body["data"]["order_id"] == "A1001"
        assert len(model.calls) == 3
        assert sum(call[0]["role"] == "system" for call in model.calls) == 1


def test_blank_message_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "   "})
    assert response.status_code == 422


def test_frontend_is_served() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        stylesheet = client.get("/static/styles.css")
        script = client.get("/static/app.js")
        tokens = client.get("/tokens.css")

    assert page.status_code == 200
    assert "对话引擎测试台" in page.text
    assert "随机会话" in page.text
    assert stylesheet.status_code == 200
    assert "macrostructure: Workbench" in stylesheet.text
    assert script.status_code == 200
    assert 'fetch("/api/chat"' in script.text
    assert "crypto?.randomUUID" in script.text
    assert tokens.status_code == 200
    assert "--color-accent" in tokens.text
