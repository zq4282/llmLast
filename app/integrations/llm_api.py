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

    def chat_completions_url(self) -> str:
        """同时支持 OpenAI SDK 风格 Base URL 和完整 Chat Completions endpoint。"""

        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        url = self.chat_completions_url()
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
        except HTTPError as exc:
            # 保留服务端返回的错误体，否则只能看到 400/401，无法排查模型名或参数。
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except OSError:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise LLMAPIError(f"LLM HTTP {exc.code} {exc.reason}{suffix}") from exc
        except URLError as exc:
            raise LLMAPIError(f"LLM 网络请求失败: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMAPIError(f"LLM 请求超时（{self.timeout} 秒）") from exc
        except json.JSONDecodeError as exc:
            raise LLMAPIError("LLM HTTP 响应不是合法 JSON") from exc

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAPIError("LLM 返回格式不正确") from exc
