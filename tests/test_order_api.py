import pytest

from app.services import order_lookup
from app.services.order_lookup import query_order


def test_query_order_accepts_dynamic_order_number() -> None:
    result = query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {"order_no": " ord202405010001 "},
        }
    )

    assert result["ok"] is True
    assert result["order_no"] == "ORD202405010001"
    assert result["amount"] == 299.0
    assert result["merchant"] == "专业版年度会员订阅"


def test_query_order_accepts_dynamic_phone_and_returns_latest_order() -> None:
    result = query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {"backup_phone": "138-0013-8000"},
        }
    )

    assert result["ok"] is True
    assert result["order_no"] == "ORD202405040004"


def test_query_order_requires_at_least_one_query_parameter() -> None:
    assert query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {},
            "call_info": {},
        }
    ) == {
        "ok": False,
        "error": "missing_order_query",
    }


def test_order_not_found_returns_a_configurable_error_code() -> None:
    result = query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {"order_no": "NOT_FOUND"},
        }
    )

    assert result == {
        "ok": False,
        "error": "order_not_found",
        "order_no": "NOT_FOUND",
    }


def test_order_number_takes_precedence_when_both_parameters_are_present() -> None:
    result = query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {
                "order_no": "ORD202405010001",
                "backup_phone": "13800138000",
            },
        }
    )

    assert result["order_no"] == "ORD202405010001"


@pytest.mark.parametrize(
    "plugin_name",
    ["refund", "unsubscribe", "refund_unsubscribe"],
)
def test_query_order_passes_current_plugin_name_to_order_api(
    monkeypatch,
    plugin_name: str,
) -> None:
    received: dict[str, str | None] = {}

    def fake_find_order(
        *,
        plugin_name: str,
        order_no: str | None = None,
        phone: str | None = None,
    ) -> dict[str, object]:
        received.update(
            plugin_name=plugin_name,
            order_no=order_no,
            phone=phone,
        )
        return {
            "ok": True,
            "order_no": "ORD202405010001",
            "merchant": "专业版年度会员订阅",
            "amount": 299.0,
        }

    monkeypatch.setattr(order_lookup, "_find_order", fake_find_order)

    result = query_order(
        {
            "business": plugin_name,
            "context": {},
            "slots": {"order_no": "ORD202405010001"},
        }
    )

    assert result["ok"] is True
    assert received == {
        "plugin_name": plugin_name,
        "order_no": "ORD202405010001",
        "phone": None,
    }


@pytest.mark.parametrize(
    ("plugin_name", "subscription_status", "expected_ok"),
    [
        ("refund", "已退订", True),
        ("unsubscribe", "已退订", False),
        ("refund_unsubscribe", "已退订", False),
        ("unsubscribe", "订阅中", True),
        ("refund_unsubscribe", "订阅中", True),
    ],
)
def test_query_order_filters_subscription_status_by_plugin(
    monkeypatch,
    plugin_name: str,
    subscription_status: str,
    expected_ok: bool,
) -> None:
    order_no = "PLUGIN_FILTER_ORDER"
    monkeypatch.setitem(
        order_lookup._ORDERS,
        order_no,
        {
            "order_no": order_no,
            "productName": "插件过滤测试订单",
            "payStatus": "支付成功",
            "payTime": "2026-09-24 10:00:00",
            "payAmount": 10.0,
            "payMethod": "微信",
            "refundStatus": "未退款",
            "refundAmount": 0.0,
            "refundTime": None,
            "refundFailReason": None,
            "subscriptionStatus": subscription_status,
            "cancelTime": None,
        },
    )

    result = query_order(
        {
            "business": plugin_name,
            "context": {},
            "slots": {"order_no": order_no},
        }
    )

    assert result["ok"] is expected_ok
    if not expected_ok:
        assert result["error"] == "order_not_found"


@pytest.mark.parametrize(
    ("pay_status", "refund_status"),
    [
        ("支付失败", "未退款"),
        ("支付成功", "退款成功"),
        ("支付成功", "退款失败"),
    ],
)
def test_query_order_rejects_unpaid_or_already_processed_refund(
    monkeypatch,
    pay_status: str,
    refund_status: str,
) -> None:
    order_no = "INELIGIBLE_ORDER"
    monkeypatch.setitem(
        order_lookup._ORDERS,
        order_no,
        {
            "order_no": order_no,
            "productName": "不可办理测试订单",
            "payStatus": pay_status,
            "payTime": "2026-09-24 10:00:00",
            "payAmount": 10.0,
            "payMethod": "微信",
            "refundStatus": refund_status,
            "refundAmount": 0.0,
            "refundTime": None,
            "refundFailReason": None,
            "subscriptionStatus": "订阅中",
            "cancelTime": None,
        },
    )

    result = query_order(
        {
            "business": "refund",
            "context": {},
            "slots": {"order_no": order_no},
        }
    )

    assert result == {
        "ok": False,
        "error": "order_not_found",
        "order_no": order_no,
    }


def test_phone_query_returns_latest_matching_order_after_plugin_filter(
    monkeypatch,
) -> None:
    phone = "13900000000"
    monkeypatch.setitem(
        order_lookup._PHONE_ORDERS,
        phone,
        {
            "ELIGIBLE_ORDER": {
                "order_no": "ELIGIBLE_ORDER",
                "productName": "仍在订阅的订单",
                "payStatus": "支付成功",
                "payTime": "2026-09-23 10:00:00",
                "payAmount": 10.0,
                "payMethod": "微信",
                "refundStatus": "未退款",
                "refundAmount": 0.0,
                "refundTime": None,
                "refundFailReason": None,
                "subscriptionStatus": "订阅中",
                "cancelTime": None,
            },
            "NEWER_INELIGIBLE_ORDER": {
                "order_no": "NEWER_INELIGIBLE_ORDER",
                "productName": "已退订的较新订单",
                "payStatus": "支付成功",
                "payTime": "2026-09-24 10:00:00",
                "payAmount": 20.0,
                "payMethod": "微信",
                "refundStatus": "未退款",
                "refundAmount": 0.0,
                "refundTime": None,
                "refundFailReason": None,
                "subscriptionStatus": "已退订",
                "cancelTime": "2026-09-24 11:00:00",
            },
        },
    )

    result = query_order(
        {
            "business": "unsubscribe",
            "context": {},
            "slots": {"backup_phone": phone},
        }
    )

    assert result["ok"] is True
    assert result["order_no"] == "ELIGIBLE_ORDER"
