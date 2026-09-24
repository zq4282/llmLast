"""账户系统适配器。"""

from datetime import date


def query_balance(account_id: str) -> dict:
    suffix = sum(ord(char) for char in account_id) % 100
    return {"ok": True, "account_id": account_id, "balance": round(50 + suffix * 1.17, 2)}


def query_bill(account_id: str, month: str | None = None) -> dict:
    month = month or date.today().strftime("%Y-%m")
    return {
        "ok": True,
        "account_id": account_id,
        "month": month,
        "amount": 88.0,
        "status": "paid",
    }


def suspend_number(phone: str, months: int = 1) -> dict:
    return {"ok": True, "phone": phone, "months": months, "status": "suspended"}
