import json

import pytest

from app.engine import graph
from app.engine.llm import TASK_ROUTER_PROMPT, IntentLLM, RouteDecision
from app.engine.loader import PluginConfigError, plugin_loader
from app.engine.route_tasks import ROUTE_TASKS, render_route_task_options
from app.integrations.llm_api import LLMAPIError


class FakeClient:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = [response] if isinstance(response, str) else list(response)
        self.messages: list[dict[str, str]] = []
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        self.messages = messages
        self.calls.append(messages)
        return self.responses.pop(0)


def test_task_router_uses_model_json_and_last_assistant_context() -> None:
    classifier = IntentLLM()
    client = FakeClient('{"task":"BUSINESS_QA","confidence":0.91}')
    classifier.client = client

    result = classifier.classify_route(
        "对",
        [{"role": "assistant", "content": "您是想了解这198元扣费吗？"}],
    )

    assert result == RouteDecision(task="BUSINESS_QA", confidence=0.91)
    assert client.messages[0]["role"] == "system"
    supplied_context = json.loads(client.messages[1]["content"])
    assert supplied_context["上一轮客服播报"] == "您是想了解这198元扣费吗？"
    assert supplied_context["当前用户表达"] == "对"


def test_router_prompt_uses_shared_task_definitions() -> None:
    assert f'"task": "{render_route_task_options()}"' in TASK_ROUTER_PROMPT
    assert "HUMAN" in ROUTE_TASKS
    assert "TRANSFER_HUMAN" not in ROUTE_TASKS


def test_missing_model_does_not_guess_route_from_keywords() -> None:
    classifier = IntentLLM()
    classifier.client = None

    with pytest.raises(LLMAPIError, match="未配置 LLM_API_KEY"):
        classifier.classify_route("不知道怎么扣的，赶紧退我钱", [])


def test_invalid_model_json_does_not_fall_back_to_keyword_rules() -> None:
    classifier = IntentLLM()
    classifier.client = FakeClient("这不是 JSON")

    with pytest.raises(LLMAPIError, match="Router 模型返回格式错误"):
        classifier.classify_route("退我钱", [])


def test_understand_renders_prompt_from_state_machine_plugin() -> None:
    classifier = IntentLLM()
    client = FakeClient(
        '{"intent":"affirm","confidence":0.95,'
        '"slots":{"backup_phone":null,"order_no":null}}'
    )
    classifier.client = client
    plugin = plugin_loader.load_all(force=True)["refund"]

    result = classifier.understand(
        "就是那笔",
        plugin,
        "CONFIRM_REFUND",
        {"amount": 29.9, "merchant": "流量包"},
        [{"role": "assistant", "content": "是否需要申请退款？"}],
    )

    assert result.intent == "affirm"
    assert result.confidence == 0.95
    prompt = client.messages[0]["content"]
    assert "当前状态：CONFIRM_REFUND" in prompt
    assert '"amount": 29.9' in prompt
    assert "系统：是否需要申请退款？" in prompt
    assert "用户当前说：就是那笔" in prompt


def test_route_task_is_declared_by_plugin_instead_of_name_alias() -> None:
    loader = plugin_loader
    loader.load_all(force=True)

    assert loader.get_by_route_task(" refund ").name == "refund"
    assert loader.get_by_route_task(" human ").name == "human"

    with pytest.raises(PluginConfigError, match="没有可处理 Task BUSINESS_QA 的插件"):
        loader.get_by_route_task("BUSINESS_QA")


def test_router_reuses_locked_shared_state_without_calling_model(monkeypatch) -> None:
    class FakePlugin:
        name = "refund"

    class FakeLoader:
        plugins = {"refund": FakePlugin()}

        @staticmethod
        def get(name: str) -> FakePlugin:
            assert name == "refund"
            return FakePlugin()

    def fail_if_called(message: str, history: list[dict[str, str]]) -> RouteDecision:
        raise AssertionError("已锁定路由时不应再请求模型")

    monkeypatch.setattr(graph, "plugin_loader", FakeLoader())
    monkeypatch.setattr(graph.intent_llm, "classify_route", fail_if_called)

    result = graph.router(
        {
            "message": "A1001",
            "history": [],
            "business": "refund",
        }
    )

    assert result["business"] == "refund"
    assert result["skip_understanding"] is False
