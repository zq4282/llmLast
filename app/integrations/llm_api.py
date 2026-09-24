"""OpenAI 兼容的 LLM HTTP 客户端。"""

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICompatibleClient:
    api_key: str
    base_url: str
    model: str
    timeout: float = 30.0

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        body = json.dumps(
            {"model": self.model, "messages": messages, "temperature": temperature},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMAPIError(f"LLM 请求失败: {exc}") from exc

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAPIError("LLM 返回格式不正确") from exc
