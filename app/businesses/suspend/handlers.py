"""停机保号业务处理函数。"""

from typing import Any

from app.integrations import account_api, sms_api


def suspend_number(state: dict[str, Any]) -> dict[str, Any]:
    phone = str(state["slots"]["phone"])
    months = int(state["slots"].get("months", 1))
    result = account_api.suspend_number(phone, months)
    if result["ok"]:
        sms_api.send_sms(phone, f"您已办理停机保号，期限 {months} 个月。")
    return result
