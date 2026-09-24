"""查询业务处理函数。"""

from typing import Any

from app.integrations import account_api


def _account_id(state: dict[str, Any]) -> str:
    return str(state["slots"].get("account_id") or state["session_id"])


def query_balance(state: dict[str, Any]) -> dict[str, Any]:
    return account_api.query_balance(_account_id(state))


def query_bill(state: dict[str, Any]) -> dict[str, Any]:
    month = state["slots"].get("month")
    if month:
        month = str(month).replace("年", "-").replace("/", "-")
    return account_api.query_bill(_account_id(state), month)
