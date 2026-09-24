from app.integrations import order_api


def test_query_order_accepts_dynamic_order_number() -> None:
    result = order_api.query_order(order_no=" ord202405010001 ")

    assert result["ok"] is True
    assert result["order_no"] == "ORD202405010001"
    assert result["amount"] == 299.0
    assert result["merchant"] == "专业版年度会员订阅"


def test_query_order_accepts_dynamic_phone_and_returns_latest_order() -> None:
    result = order_api.query_order(phone="176-0018-4282")

    assert result["ok"] is True
    assert result["order_no"] == "ORD202405040004"


def test_query_order_requires_at_least_one_query_parameter() -> None:
    assert order_api.query_order() == {
        "ok": False,
        "error": "missing_order_query",
    }


def test_order_not_found_returns_a_configurable_error_code() -> None:
    result = order_api.query_order(order_no="NOT_FOUND")

    assert result == {
        "ok": False,
        "error": "order_not_found",
        "order_no": "NOT_FOUND",
    }


def test_order_number_takes_precedence_when_both_parameters_are_present() -> None:
    result = order_api.query_order(
        order_no="ORD202405010001",
        phone="17600184282",
    )

    assert result["order_no"] == "ORD202405010001"
