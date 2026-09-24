"""统一业务动作的返回格式，让后续节点知道动作成功了还是失败了。"""

from typing import Any, TypeAlias

from app.engine.constants import ActionResultField


ActionResult: TypeAlias = dict[str, Any]


def _reject_reserved_fields(data: dict[str, Any]) -> None:
    # ok 和 error 由这里统一填写，业务数据不能覆盖它们。
    reserved = {ActionResultField.OK, ActionResultField.ERROR} & data.keys()
    if reserved:
        names = ", ".join(sorted(reserved))
        raise ValueError(f"{names} 是 action 返回协议保留字段")


def action_success(**data: Any) -> ActionResult:
    """标记动作成功；附带的数据可以留到下一轮，也可以填进回复。"""

    _reject_reserved_fields(data)
    return {ActionResultField.OK: True, **data}


def action_failure(error: str, **data: Any) -> ActionResult:
    """标记动作失败；错误原因会决定该怎么回复用户。"""

    _reject_reserved_fields(data)
    if not error:
        raise ValueError("action_failure 的 error 不能为空")
    return {ActionResultField.OK: False, ActionResultField.ERROR: error, **data}


def validate_action_result(result: object, source: str) -> ActionResult:
    """动作一执行完就检查返回值，别等到拼回复时才发现格式不对。"""

    if not isinstance(result, dict):
        raise TypeError(f"{source} 必须返回 dict")
    if not isinstance(result.get(ActionResultField.OK), bool):
        raise TypeError(f"{source} 返回值必须包含布尔字段 ok")
    if result[ActionResultField.OK] is False:
        error = result.get(ActionResultField.ERROR)
        if not isinstance(error, str) or not error:
            raise TypeError(f"{source} 失败时必须返回非空字符串字段 error")
    return result
