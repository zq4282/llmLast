"""把用户的话交给模型，分别判断要办哪项业务、这句话是什么意思。"""

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.engine.constants import OtherDecisionType, OtherIntent, SwitchDecisionType
from app.engine.loader import Plugin
from app.engine.route_tasks import (
    ROUTE_TASKS,
    render_route_task_definitions,
    render_route_task_options,
)
from app.integrations.llm_api import LLMAPIError, OpenAICompatibleClient

TASKS = ROUTE_TASKS

# 顶层只选业务，不查询订单，也不直接决定办理结果。
TASK_ROUTER_PROMPT = """# Role

语音客服系统的【任务路由识别器 Task Router】。
唯一职责：分析用户当前的表达（必要时参考上一轮对话上下文），将其分类映射至唯一的预设 Task。仅负责分类识别，不执行业务逻辑。

## 一、数据特性（ASR 文本容错）

当前输入由语音识别（ASR）直接转写生成，**可能存在同音错别字、口语吞音、连字、无标点或无意义语气词**（例：“退前”->“退钱”、“退狂”->“退款”、“转仁工”->“转人工”）。请结合语境及发音容错还原真实含义，切勿仅因错别字直接归为 UNKNOWN。

## 二、核心判定原则

1. **当前句优先**：若用户当前意图明确，直接判定，不回溯历史。
2. **上下文按需补全**：仅当用户表述为代词指代（如“就这个”、“弄它”）或短促确认（如“对”、“可以”、“赶紧办”）时，才参考上一轮客服播报补全语义；结合后仍指向不明则归为 UNKNOWN。
3. **诉求优先于情绪与原因**：
   - 情绪（愤怒、催促、不满）不代表具体意图，严禁因情绪推断任务。
   - 复合诉求中，以“最终动作落脚点”为主任务（例：“不知道怎么扣的，赶紧退我钱” -> 主诉求为退款，判定为 REFUND）。
4. **无明确任务指向的弱应答**：若用户仅输入“好的”、“行”、“对”，且上一轮并未发起与特定 Task 相关的意图确认，统一判定为 UNKNOWN。

## 三、Task 枚举与边界

__TASK_DEFINITIONS__

## 四、禁止事项

- 严禁执行查询、退款等业务动作或给出解答建议。
- 严禁把“质疑扣费/表达不满”直接等同于“要求退款”。
- 严禁在 JSON 之外输出任何解释、分析或前置后置文字。

## 五、输出格式

必须严格仅输出标准 JSON 格式。`confidence` 为 **0.0 到 1.0 之间的浮点数（保留两位小数）**：

- **0.85 ~ 1.00**：意图极明确、关键词完备（如包含明确动词且无歧义）。
- **0.60 ~ 0.84**：依赖上下文补全、存在轻度 ASR 谐音推断，或表达略显口语化但主体明确。
- **0.00 ~ 0.59**：语义严重缺失、多意图混杂冲突、或归入 UNKNOWN。
```json
{
  "task": "__TASK_OPTIONS__",
  "confidence": 0.95
}
```""".replace(
    "__TASK_DEFINITIONS__", render_route_task_definitions()
).replace(
    "__TASK_OPTIONS__", render_route_task_options()
)


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
    """让模型负责分类；程序只检查模型回答是否符合约定格式。"""

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

        if self.client is None:
            raise LLMAPIError("未配置 LLM_API_KEY，无法执行顶层路由")
        return self._route_with_llm(message, history)

    def _route_with_llm(
            self,
            message: str,
            history: list[dict[str, str]],
    ) -> RouteDecision:
        # 只带上一轮客服回复，短句如“就这个”才有足够上下文可判断。
        previous_assistant = next(
            (item.get("content", "") for item in reversed(history) if item.get("role") == "assistant"),
            "",
        )
        user_input = json.dumps(
            {"上一轮客服播报": previous_assistant, "当前用户表达": message},
            ensure_ascii=False,
        )
        content = self.client.chat(
            [
                {"role": "system", "content": TASK_ROUTER_PROMPT},
                {"role": "user", "content": user_input},
            ]
        )
        if not isinstance(content, str):
            raise LLMAPIError("Router 模型返回了非文本内容")
        try:
            data = self._parse_json_object(content)
            task = str(data.get("task", "")).upper()
            confidence = float(data.get("confidence"))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMAPIError(f"Router 模型返回格式错误: {content[:300]!r}") from exc
        if task not in TASKS:
            raise LLMAPIError(f"Router 模型返回未知 Task: {task!r}")
        if not 0.0 <= confidence <= 1.0:
            raise LLMAPIError(f"Router 模型返回非法 confidence: {confidence!r}")
        return RouteDecision(task=task, confidence=round(confidence, 2))

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        """去掉模型偶尔附带的代码框，再确认内容确实是 JSON 对象。"""

        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise TypeError("LLM 路由结果必须是 JSON object")
        return data

    def understand(
            self,
            message: str,
            plugin: Plugin,
            plugin_state: str,
            context: dict[str, Any],
            history: list[dict[str, str]],
    ) -> IntentDecision:
        """用当前业务的提示词理解这一句，不在这里执行任何业务动作。"""

        if self.client is None:
            raise LLMAPIError("未配置 LLM_API_KEY，无法执行插件意图识别")
        return self._understand_with_llm(message, plugin, plugin_state, context, history)

    def _understand_with_llm(
            self,
            message: str,
            plugin: Plugin,
            plugin_state: str,
            context: dict[str, Any],
            history: list[dict[str, str]],
    ) -> IntentDecision:
        # 插件只看最近十条对话，避免旧话题干扰当前业务判断。
        history_text = "\n".join(
            f"{'系统' if item.get('role') == 'assistant' else '用户'}：{item.get('content', '')}"
            for item in history[-10:]
        ) or "（无）"
        prompt = plugin.prompt
        replacements = {
            "{state}": plugin_state,
            "{context}": json.dumps(context, ensure_ascii=False),
            "{history}": history_text,
            "{user_input}": message,
        }
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)

        content = self.client.chat([{"role": "user", "content": prompt}])
        if not isinstance(content, str):
            raise LLMAPIError(f"插件 {plugin.name} 模型返回了非文本内容")
        try:
            data = self._parse_json_object(content)
            intent = data.get("intent")
            slots = data.get("slots")
            confidence = float(data.get("confidence"))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMAPIError(
                f"插件 {plugin.name} 模型返回格式错误: {content[:300]!r}"
            ) from exc
        if not isinstance(intent, str) or not intent:
            raise LLMAPIError(f"插件 {plugin.name} 模型未返回有效 intent")
        if not isinstance(slots, dict):
            raise LLMAPIError(f"插件 {plugin.name} 模型返回的 slots 不是 object")
        if not 0.0 <= confidence <= 1.0:
            raise LLMAPIError(f"插件 {plugin.name} 模型返回非法 confidence: {confidence!r}")
        # 没提到的字段不写入结果，避免把已有订单号等信息清空。
        explicit_slots = {str(key): value for key, value in slots.items() if value is not None}
        return IntentDecision(intent, round(confidence, 2), explicit_slots)

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

        if self.client is None:
            raise LLMAPIError("未配置 LLM_API_KEY，无法执行 other 插件")
        task_names = " | ".join(available_tasks) or "（无）"
        task_definitions = "\n".join(
            f"- {task}：{description}" for task, description in available_tasks.items()
        ) or "（当前没有可转入的固定业务）"
        prompt = plugin.prompt
        replacements = {
            "{system_prompt}": system_prompt,
            "{available_tasks}": task_names,
            "{task_definitions}": task_definitions,
            "{user_input}": message,
            "{max_reply_len}": str(max_reply_len),
        }
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)
        content = self.client.chat([{"role": "user", "content": prompt}])
        if not isinstance(content, str):
            raise LLMAPIError("other 插件模型返回了非文本内容")
        try:
            data = self._parse_json_object(content)
            decision = str(data.get("decision", "")).strip().upper()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMAPIError(f"other 插件模型返回格式错误: {content[:300]!r}") from exc

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

    def confirm_switch(self, message: str) -> SwitchConfirmationDecision:
        """只判断用户是同意切换、取消切换，还是没有说清楚。"""

        if self.client is None:
            raise LLMAPIError("未配置 LLM_API_KEY，无法确认插件切换")
        prompt = f"""你是业务流程切换确认器。用户上一轮被询问是否暂停当前业务并切换到另一个业务。
只判断用户当前回答：确认切换、取消切换，还是没有明确回答。
用户当前说：{message}
只输出 JSON：{{"decision":"CONFIRM|CANCEL|UNKNOWN"}}"""
        content = self.client.chat([{"role": "user", "content": prompt}])
        if not isinstance(content, str):
            raise LLMAPIError("切换确认模型返回了非文本内容")
        try:
            data = self._parse_json_object(content)
            decision = str(data.get("decision", "")).strip().upper()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMAPIError(f"切换确认模型返回格式错误: {content[:300]!r}") from exc
        if decision not in set(SwitchDecisionType):
            raise LLMAPIError(f"切换确认模型返回未知 decision: {decision!r}")
        return SwitchConfirmationDecision(SwitchDecisionType(decision))


intent_llm = IntentLLM()
