"""业务 action handler 的统一返回协议。"""

from typing import Any, TypeAlias


ActionResult: TypeAlias = dict[str, Any]


def _reject_reserved_fields(data: dict[str, Any]) -> None:
    reserved = {"ok", "error"} & data.keys()
    if reserved:
        names = ", ".join(sorted(reserved))
        raise ValueError(f"{names} 是 action 返回协议保留字段")


def action_success(**data: Any) -> ActionResult:
    """构造成功结果；data 会进入会话 context，也可用于渲染回复话术。"""

    _reject_reserved_fields(data)
    return {"ok": True, **data}


def action_failure(error: str, **data: Any) -> ActionResult:
    """构造失败结果；error 对应 plugin.yaml 中 on_error 的错误码。"""

    _reject_reserved_fields(data)
    if not error:
        raise ValueError("action_failure 的 error 不能为空")
    return {"ok": False, "error": error, **data}


def validate_action_result(result: object, source: str) -> ActionResult:
    """校验 handler 返回值，避免错误结果流入 reply 节点后才暴露。"""

    if not isinstance(result, dict):
        raise TypeError(f"{source} 必须返回 dict")
    if not isinstance(result.get("ok"), bool):
        raise TypeError(f"{source} 返回值必须包含布尔字段 ok")
    if result["ok"] is False:
        error = result.get("error")
        if not isinstance(error, str) or not error:
            raise TypeError(f"{source} 失败时必须返回非空字符串字段 error")
    return result
