"""聊天接口的请求与响应模型。"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _current_time_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CamelCaseModel(BaseModel):
    """接口使用 camelCase，同时允许 Python 内部按 snake_case 访问。"""

    model_config = ConfigDict(populate_by_name=True)


class CallInfo(CamelCaseModel):
    caller: str = Field(default="13800138000", min_length=1, max_length=32)
    callee: str = Field(default="10000", min_length=1, max_length=32)
    call_start_time: str = Field(default_factory=_current_time_text, alias="callStartTime")


class HistoryContextItem(CamelCaseModel):
    round: int = Field(ge=1)
    user_text: str = Field(alias="userText", min_length=1, max_length=4000)
    ai_text: str = Field(alias="aiText", min_length=1, max_length=4000)
    time: str = Field(default_factory=_current_time_text)


class ChatConfig(CamelCaseModel):
    max_reply_len: int = Field(default=60, alias="maxReplyLen", ge=1, le=4000)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class ChatRequest(CamelCaseModel):
    """一次通话对话请求。未提供的控制字段使用业务默认值。"""

    session_id: str | None = Field(default=None, alias="sessionId", max_length=128)
    tenant_id: int = Field(default=1002, alias="tenantId", ge=1)
    call_info: CallInfo = Field(default_factory=CallInfo, alias="callInfo")
    system_prompt: str = Field(
        default="你是会员业务客服，请识别用户意图并生成回复话术",
        alias="systemPrompt",
        max_length=4000,
    )
    history_context: list[HistoryContextItem] = Field(default_factory=list, alias="historyContext")
    current_user_text: str = Field(alias="currentUserText", min_length=1, max_length=4000)
    config: ChatConfig = Field(default_factory=ChatConfig)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_request(cls, value: Any) -> Any:
        """平滑兼容旧客户端；OpenAPI 与新前端只展示新格式。"""

        if isinstance(value, dict) and "currentUserText" not in value and "current_user_text" not in value:
            legacy_message = value.get("message")
            if legacy_message is not None:
                value = dict(value)
                value["currentUserText"] = legacy_message
        return value

    @field_validator("current_user_text")
    @classmethod
    def current_user_text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("currentUserText 不能为空")
        return value

    def resolved_session_id(self) -> str:
        return self.session_id or f"CALL_{uuid4().hex[:12].upper()}"

    def engine_history(self) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for item in self.history_context:
            history.extend(
                [
                    {"role": "user", "content": item.user_text},
                    {"role": "assistant", "content": item.ai_text},
                ]
            )
        return history


class ChatResponse(BaseModel):
    """对外保持稳定的统一响应。"""

    session_id: str
    reply: str
    business: str
    intent: str
    action: str | None = None
    out: str = "CHAT"
    data: dict[str, Any] = Field(default_factory=dict)
