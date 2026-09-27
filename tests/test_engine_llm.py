"""验证四类模型请求共用入口后的错误处理和场景边界。"""

from dataclasses import replace

import json

import pytest

from app.engine.llm import IntentLLM
from app.engine.llm_prompts import build_other_messages, build_route_messages, build_understand_messages
from app.engine.loader import plugin_loader
from app.integrations.llm_api import LLMAPIError


class FakeClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture(params=["route", "understand", "other", "switch"])
def scene(request):
    llm = IntentLLM()
    plugins = plugin_loader.load_all()
    calls = {
        "route": lambda: llm.classify_route("退款", []),
        "understand": lambda: llm.understand("退款", plugins["refund"], "IDLE", {}, []),
        "other": lambda: llm.handle_other("你好", plugins["other"], {"REFUND": "退款"}),
        "switch": lambda: llm.confirm_switch("确认切换"),
    }
    labels = {
        "route": "Router 模型",
        "understand": "插件 refund 模型",
        "other": "other 插件模型",
        "switch": "切换确认模型",
    }
    return llm, calls[request.param], labels[request.param]


@pytest.mark.parametrize("response", ["非 JSON", "[]", "```json\n[]\n```", None])
def test_all_scenes_reject_invalid_model_output_with_scene_name(scene, response) -> None:
    llm, invoke, label = scene
    client = FakeClient(response)
    llm.client = client

    with pytest.raises(LLMAPIError, match=f"{label}返回"):
        invoke()
    assert len(client.calls) == 1


def test_all_scenes_report_missing_configuration(scene) -> None:
    llm, invoke, _ = scene
    llm.client = None
    with pytest.raises(LLMAPIError, match="未配置 LLM_API_KEY"):
        invoke()


def test_all_scenes_preserve_transport_errors(scene) -> None:
    llm, invoke, _ = scene
    failure = LLMAPIError("模型请求超时")
    llm.client = FakeClient(failure)
    with pytest.raises(LLMAPIError) as caught:
        invoke()
    assert caught.value is failure


@pytest.mark.parametrize("response, error", [
    ('{"decision":"ROUTE","target_task":"UNKNOWN_TASK"}', "不可用 target_task"),
    ('{"decision":"ANSWER","intent":"chitchat","reply":" "}', "未返回有效 reply"),
    ('{"decision":"ANSWER","intent":"invented","reply":"你好"}', "未知 intent"),
])
def test_other_still_validates_answer_and_route(response: str, error: str) -> None:
    llm = IntentLLM()
    llm.client = FakeClient(response)
    plugin = plugin_loader.load_all()["other"]
    with pytest.raises(LLMAPIError, match=error):
        llm.handle_other("你好", plugin, {"REFUND": "退款"})


def test_switch_accepts_code_fences_and_rejects_unknown_decisions() -> None:
    llm = IntentLLM()
    llm.client = FakeClient('```json\n{"decision":" confirm "}\n```')
    assert llm.confirm_switch("确认切换").decision == "CONFIRM"
    llm.client = FakeClient('{"decision":"YES"}')
    with pytest.raises(LLMAPIError, match="未知 decision"):
        llm.confirm_switch("确认切换")


def test_understand_keeps_all_history_and_json_examples() -> None:
    plugin = replace(
        plugin_loader.load_all()["refund"],
        prompt='{state}\n{context}\n{history}\n{user_input}\n{"intent":"affirm"}',
    )
    history = [{"role": "user", "content": "旧话题"}]
    history += [{"role": "assistant", "content": f"最近回复{i}"} for i in range(10)]
    messages = build_understand_messages("确认", plugin, "CONFIRM_REFUND", {"金额": 29.9}, history)
    prompt = messages[0]["content"]
    assert "用户：旧话题" in prompt
    assert all(f"系统：最近回复{i}" in prompt for i in range(10))
    assert '"金额": 29.9' in prompt
    assert '{"intent":"affirm"}' in prompt
    assert plugin.prompt.startswith("{state}")


def test_other_messages_keep_identity_and_empty_task_fallbacks() -> None:
    plugin = plugin_loader.load_all()["other"]
    messages = build_other_messages(
        "你是谁", plugin, {}, system_prompt="你是客服小明", max_reply_len=30,
    )
    prompt = messages[0]["content"]
    assert "客服身份：你是客服小明" in prompt
    assert "（当前没有可转入的固定业务）" in prompt
    assert "用户当前说：你是谁" in prompt
    assert "30 个中文字符" in prompt


def test_route_keeps_full_history_beyond_previous_storage_limit() -> None:
    history = []
    for turn in range(25):
        history.extend([
            {"role": "user", "content": f"用户问题{turn}"},
            {"role": "assistant", "content": f"客服回复{turn}"},
        ])
    messages = build_route_messages("对", history)
    supplied_context = json.loads(messages[1]["content"])
    assert supplied_context["历史对话"] == history
    assert supplied_context["上一轮客服播报"] == "客服回复24"
    assert supplied_context["当前用户表达"] == "对"
