"""短信服务适配器。"""


def send_sms(phone: str, content: str) -> dict:
    # 示例项目不实际发送短信，生产环境在这里接供应商 SDK。
    return {"ok": True, "phone": phone, "content": content, "provider": "mock"}
