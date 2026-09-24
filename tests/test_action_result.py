import pytest

from app.engine.action_result import (
    action_failure,
    action_success,
    validate_action_result,
)


def test_action_result_helpers_make_contract_explicit() -> None:
    assert action_success(order_no="A1001") == {"ok": True, "order_no": "A1001"}
    assert action_failure("upstream_timeout", order_no="A404") == {
        "ok": False,
        "error": "upstream_timeout",
        "order_no": "A404",
    }


@pytest.mark.parametrize(
    "result, message",
    [
        (None, "必须返回 dict"),
        ({"order_no": "A1001"}, "必须包含布尔字段 ok"),
        ({"ok": False}, "失败时必须返回非空字符串字段 error"),
    ],
)
def test_action_result_validation_rejects_invalid_contract(
    result: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        validate_action_result(result, "refund.example")


def test_action_result_helpers_protect_reserved_fields() -> None:
    with pytest.raises(ValueError, match="ok 是 action 返回协议保留字段"):
        action_success(ok=False)
    with pytest.raises(ValueError, match="error 不能为空"):
        action_failure("")
