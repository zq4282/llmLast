from fastapi.testclient import TestClient

from app.main import app
from app.session.store import session_store


def test_chat_api_and_session_follow_up() -> None:
    session_store.clear()
    with TestClient(app) as client:
        first = client.post("/api/chat", json={"session_id": "api-test", "message": "办理停机保号"})
        assert first.status_code == 200
        assert first.json()["business"] == "suspend"
        assert "手机号" in first.json()["reply"]

        second = client.post("/api/chat", json={"session_id": "api-test", "message": "13800138000，办理3个月"})
        assert second.status_code == 200
        body = second.json()
        assert body["data"]["phone"] == "13800138000"
        assert body["data"]["months"] == 3


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
    assert stylesheet.status_code == 200
    assert "macrostructure: Workbench" in stylesheet.text
    assert script.status_code == 200
    assert 'fetch("/api/chat"' in script.text
    assert tokens.status_code == 200
    assert "--color-accent" in tokens.text
