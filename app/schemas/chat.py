"""聊天接口的请求与响应模型。"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """一次对话请求；不传 session_id 时自动创建新会话。"""

    message: str = Field(min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message 不能为空")
        return value

    def resolved_session_id(self) -> str:
        return self.session_id or uuid4().hex


class ChatResponse(BaseModel):
    """对外保持稳定的统一响应。"""

    session_id: str
    reply: str
    business: str
    intent: str
    action: str | None = None
    out: str = "CHAT"
    data: dict[str, Any] = Field(default_factory=dict)
