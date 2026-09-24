"""意图识别与可选真实 LLM 调用。"""

import json
import re
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.engine.loader import Plugin
from app.integrations.llm_api import LLMAPIError, OpenAICompatibleClient


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str = "sk-ws-H.PLHRLEE.rtWE.MEYCIQDgm_ni_Cb8BdPBa384kCTfU8QWngbpbksHFkP1Tg5nGgIhAIE09-_l0H6-zWQCV0DZsU_ZRtTqw6Mb4KD1Iqk1lJvk"
    llm_base_url: str = "https://ws-n6nxk2gz1r2ars3h.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-turbo"
    llm_timeout: float = 4.0


class IntentLLM:
    """有 Key 时可调用真实模型；失败时自动回退到确定性规则。"""

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

    def route(self, message: str, plugins: dict[str, Plugin], active_business: str | None) -> str:
        scores = {
            name: sum(2 if keyword in message else 0 for keyword in plugin.keywords)
            for name, plugin in plugins.items()
            if name != "chat"
        }
        best = max(scores, key=scores.get) if scores else "chat"
        if scores and scores[best] > 0:
            return best
        if active_business and active_business in plugins and active_business != "chat":
            return active_business
        return "chat"

    def understand(
        self,
        message: str,
        plugin: Plugin,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if self.client:
            llm_result = self._understand_with_llm(message, plugin)
            if llm_result:
                intent, slots = llm_result
                return intent, {**context, **slots, "_intent": intent}

        scores = {
            name: sum(1 for keyword in intent.keywords if keyword in message)
            for name, intent in plugin.intents.items()
        }
        best = max(scores, key=scores.get)
        previous_intent = context.get("_intent")
        intent_name = best if scores[best] > 0 else (
            previous_intent if previous_intent in plugin.intents else plugin.default_intent
        )
        slots = dict(context)
        for config in plugin.intents.values():
            for slot_name, pattern in config.slot_patterns.items():
                match = re.search(pattern, message, flags=re.IGNORECASE)
                if match:
                    value = match.groupdict().get("value") if match.groupdict() else match.group(1)
                    slots[slot_name] = value.strip()
        slots["_intent"] = intent_name
        return intent_name, slots

    def _understand_with_llm(self, message: str, plugin: Plugin) -> tuple[str, dict[str, Any]] | None:
        schema = {
            name: {"keywords": list(intent.keywords), "slots": list(intent.slot_patterns)}
            for name, intent in plugin.intents.items()
        }
        prompt = (
            "识别用户意图并抽取槽位。只返回 JSON："
            '{"intent":"...","slots":{}}。可选意图：'
            f"{json.dumps(schema, ensure_ascii=False)}\n用户：{message}"
        )
        try:
            content = self.client.chat([{"role": "user", "content": prompt}]) if self.client else ""
            content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
            data = json.loads(content)
            intent = data.get("intent")
            if intent in plugin.intents and isinstance(data.get("slots", {}), dict):
                return intent, data["slots"]
        except (LLMAPIError, json.JSONDecodeError, TypeError):
            return None
        return None


intent_llm = IntentLLM()
