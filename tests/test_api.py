import pytest

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
        assert ended_session.call_info["caller"] == "13800138000"
        assert ended_session.context == {}
        assert ended_session.history[-1]["content"] == end_response.json()["reply"]
        assert ended_session.flows[-1]["status"] == "COMPLETED"

        assert len(model.calls) == 4
        assert sum(call[0]["role"] == "system" for call in model.calls) == 1


def test_blank_message_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"currentUserText": "   "})
    assert response.status_code == 422


def test_api_keeps_completed_session_after_handoff(monkeypatch) -> None:
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
        assert len(stored.history) == 8
        assert stored.flows[-1]["status"] == "COMPLETED"
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
    assert 'new Set(["HUMAN", "END"])' in script.text
    assert "elements.input.disabled = isTerminal" in script.text
    assert "当前会话已结束，请点击“新建会话”后继续" in script.text
    assert "CALL_001" not in page.text
    assert tokens.status_code == 200
    assert "--color-accent" in tokens.text


@pytest.mark.parametrize("chitchat_rounds", [0, 12])
def test_cancelled_refund_keeps_full_history_and_can_restart_after_many_turns(monkeypatch, chitchat_rounds) -> None:
    # 模拟识别结果，验证查询、状态和持久化；此测试不评估真实模型的语义准确率。
    responses = [
        '{"task":"REFUND","confidence":0.97}',
        '{"intent":"refund_request","confidence":0.98,"slots":{}}',
        '{"intent":"unknown","confidence":0.3,"slots":{}}',
        '{"intent":"unknown","confidence":0.3,"slots":{}}',
        '{"intent":"negate","confidence":0.98,"slots":{}}',
    ]
    for _ in range(chitchat_rounds):
        responses.extend([
            '{"intent":"other","confidence":0.98,"slots":{}}',
            '{"decision":"ANSWER","intent":"chitchat","reply":"您好，有什么可以帮您？"}',
        ])
    responses.extend([
        '{"intent":"refund_request","confidence":0.98,"slots":{}}',
        '{"intent":"affirm","confidence":0.98,"slots":{}}',
    ])
    model = SequenceClient(responses)
    monkeypatch.setattr(intent_llm, "client", model)
    session_store.clear()
    session_id = f"refund-long-chat-{chitchat_rounds}"
    with TestClient(app) as client:
        def send(message):
            response = client.post("/api/chat", json={"sessionId": session_id, "currentUserText": message})
            assert response.status_code == 200
            return response.json()

        first = send("为啥扣我23元，我要退款")
        assert "19.9元" in first["reply"]
        first_flow = session_store.get(session_id).active_flow_id
        send("啥玩意")
        send("我也不知道呢")
        cancelled = send("不退款")
        assert "已取消退款" in cancelled["reply"]
        saved = session_store.get(session_id)
        assert saved.plugin_state == "IDLE"
        assert saved.unrecognized_count == 0
        order_no = saved.context["order_no"]

        for _ in range(chitchat_rounds):
            send("你好")
            saved = session_store.get(session_id)
            assert saved.active_flow_id == first_flow
            assert saved.plugin_state == "IDLE"
            assert saved.context["order_no"] == order_no
        assert saved.history[0]["content"] == "为啥扣我23元，我要退款"
        assert len(saved.history) == 2 * (4 + chitchat_rounds)

        restarted = send("我要退款")
        assert restarted["intent"] == "refund_request"
        assert restarted["action"] == "query_order"
        assert "19.9元" in restarted["reply"]
        assert "是否需要为您申请退款" in restarted["reply"]
        saved = session_store.get(session_id)
        assert saved.plugin_state == "CONFIRM_REFUND"
        assert saved.context["order_no"] == order_no
        # 对话轮数增加后，模型仍收到原始问题和独立保存的订单上下文。
        assert order_no in model.calls[-1][0]["content"]
        assert "用户：为啥扣我23元，我要退款" in model.calls[-1][0]["content"]
        confirmed = send("确认退款")
        assert confirmed["out"] == "REFUND"
        assert confirmed["data"]["order_no"] == order_no
        assert model.responses == []
