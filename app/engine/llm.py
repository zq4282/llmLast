"""组织模型请求，并把各场景的 JSON 回答校验为业务决策。"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.engine.constants import OtherDecisionType, OtherIntent, SwitchDecisionType
from app.engine.llm_prompts import (
    TASK_ROUTER_PROMPT,  # 保留原来的导入入口。
    build_other_messages,
    build_route_messages,
    build_switch_messages,
    build_understand_messages,
)
from app.engine.loader import Plugin
from app.engine.route_tasks import ROUTE_TASKS
from app.integrations.llm_api import LLMAPIError, OpenAICompatibleClient

TASKS = ROUTE_TASKS
DecisionT = TypeVar("DecisionT")


@dataclass(frozen=True)
class RouteDecision:
    task: str
    confidence: float


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    confidence: float
    slots: dict[str, Any]


@dataclass(frozen=True)
class OtherDecision:
    decision: str
    intent: str | None
    reply: str | None
    target_task: str | None


@dataclass(frozen=True)
class SwitchConfirmationDecision:
    decision: str


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "qwen-turbo"
    llm_timeout: float = 4.0


class IntentLLM:
    """按场景构建消息，共用请求入口，再分别校验业务结果。"""

    def __init__(self) -> None:
        settings = LLMSettings()
        api_key = settings.llm_api_key.strip()
        self.client = (
            OpenAICompatibleClient(
                api_key=api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
            )
            if api_key
            else None
        )

    def classify_route(self, message: str, history: list[dict[str, str]]) -> RouteDecision:
        """只判断要办哪项业务；已有业务是否继续由外层流程决定。"""

        return self._request_decision(
            build_route_messages(message, history),
            parse=self._parse_route_decision,
            label="Router 模型",
            purpose="执行顶层路由",
        )

    def understand(
        self,
        message: str,
        plugin: Plugin,
        plugin_state: str,
        context: dict[str, Any],
        history: list[dict[str, str]],
    ) -> IntentDecision:
        """用当前业务的提示词理解这一句，不在这里执行任何业务动作。"""

        return self._request_decision(
            build_understand_messages(message, plugin, plugin_state, context, history),
            parse=lambda data: self._parse_intent_decision(data, plugin.name),
            label=f"插件 {plugin.name} 模型",
            purpose="执行插件意图识别",
        )

    def handle_other(
        self,
        message: str,
        plugin: Plugin,
        available_tasks: dict[str, str],
        *,
        system_prompt: str = "你是会员业务客服",
        max_reply_len: int = 60,
    ) -> OtherDecision:
        """让 other 回答当前插话或选新业务，不向它透露原业务进度。"""

        return self._request_decision(
            build_other_messages(
                message, plugin, available_tasks,
                system_prompt=system_prompt, max_reply_len=max_reply_len,
            ),
            parse=lambda data: self._parse_other_decision(data, available_tasks),
            label="other 插件模型",
            purpose="执行 other 插件",
        )

    def confirm_switch(self, message: str) -> SwitchConfirmationDecision:
        """只判断用户是同意切换、取消切换，还是没有说清楚。"""

        return self._request_decision(
            build_switch_messages(message),
            parse=self._parse_switch_decision,
            label="切换确认模型",
            purpose="确认插件切换",
        )

    def _request_decision(
        self,
        messages: list[dict[str, str]],
        *,
        parse: Callable[[dict[str, Any]], DecisionT],
        label: str,
        purpose: str,
    ) -> DecisionT:
        """统一检查客户端、发起请求、解析 JSON，并标明失败的调用场景。"""

        if self.client is None:
            raise LLMAPIError(f"未配置 LLM_API_KEY，无法{purpose}")
        content = self.client.chat(messages)
        if not isinstance(content, str):
            raise LLMAPIError(f"{label}返回了非文本内容")
        try:
            return parse(self._parse_json_object(content))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMAPIError(f"{label}返回格式错误: {content[:300]!r}") from exc

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        """去掉模型偶尔附带的代码框，再确认内容确实是 JSON 对象。"""

        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise TypeError("LLM 路由结果必须是 JSON object")
        return data

    @staticmethod
    def _parse_route_decision(data: dict[str, Any]) -> RouteDecision:
        """顶层路由必须返回已知 Task 和合法置信度。"""

        task = str(data.get("task", "")).upper()
        confidence = float(data.get("confidence"))
        if task not in TASKS:
            raise LLMAPIError(f"Router 模型返回未知 Task: {task!r}")
        if not 0.0 <= confidence <= 1.0:
            raise LLMAPIError(f"Router 模型返回非法 confidence: {confidence!r}")
        return RouteDecision(task=task, confidence=round(confidence, 2))

    @staticmethod
    def _parse_intent_decision(data: dict[str, Any], plugin_name: str) -> IntentDecision:
        """业务理解必须返回意图、槽位对象和合法置信度。"""

        intent = data.get("intent")
        slots = data.get("slots")
        confidence = float(data.get("confidence"))
        if not isinstance(intent, str) or not intent:
            raise LLMAPIError(f"插件 {plugin_name} 模型未返回有效 intent")
        if not isinstance(slots, dict):
            raise LLMAPIError(f"插件 {plugin_name} 模型返回的 slots 不是 object")
        if not 0.0 <= confidence <= 1.0:
            raise LLMAPIError(f"插件 {plugin_name} 模型返回非法 confidence: {confidence!r}")
        # 没提到的字段不写入结果，避免把已有订单号等信息清空。
        explicit_slots = {str(key): value for key, value in slots.items() if value is not None}
        return IntentDecision(intent, round(confidence, 2), explicit_slots)

    @staticmethod
    def _parse_other_decision(
        data: dict[str, Any], available_tasks: dict[str, str],
    ) -> OtherDecision:
        """闲聊需有有效回答，业务分流只能选择已安装的任务。"""

        decision = str(data.get("decision", "")).strip().upper()
        if decision == OtherDecisionType.ANSWER:
            intent = data.get("intent")
            reply = data.get("reply")
            if intent not in set(OtherIntent):
                raise LLMAPIError(f"other 插件返回未知 intent: {intent!r}")
            if not isinstance(reply, str) or not reply.strip():
                raise LLMAPIError("other 插件 ANSWER 未返回有效 reply")
            return OtherDecision(OtherDecisionType.ANSWER, intent, reply.strip(), None)

        if decision == OtherDecisionType.ROUTE:
            # 只能转到已经装好的业务；模型编出的任务名不能直接执行。
            target_task = str(data.get("target_task", "")).strip().upper()
            if target_task not in available_tasks:
                raise LLMAPIError(f"other 插件返回不可用 target_task: {target_task!r}")
            return OtherDecision(OtherDecisionType.ROUTE, None, None, target_task)

        raise LLMAPIError(f"other 插件返回未知 decision: {decision!r}")

    @staticmethod
    def _parse_switch_decision(data: dict[str, Any]) -> SwitchConfirmationDecision:
        """切换确认只接受约定的三种决定。"""

        decision = str(data.get("decision", "")).strip().upper()
        if decision not in set(SwitchDecisionType):
            raise LLMAPIError(f"切换确认模型返回未知 decision: {decision!r}")
        return SwitchConfirmationDecision(SwitchDecisionType(decision))


intent_llm = IntentLLM()
