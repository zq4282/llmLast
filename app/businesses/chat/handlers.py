"""兜底聊天业务。"""

from typing import Any


def general_chat(state: dict[str, Any]) -> dict[str, Any]:
    message = state["message"]
    if any(word in message.lower() for word in ("你好", "hello", "hi")):
        answer = "你好！我可以帮你办理退款、查询余额或账单，以及停机保号。"
    else:
        answer = "我可以协助退款、余额/账单查询和停机保号，请告诉我你想办理什么。"
    return {"ok": True, "answer": answer}
