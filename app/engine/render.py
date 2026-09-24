"""把订单金额等实际数据填进业务回复模板。"""

from typing import Any, Mapping


class _SafeValues(dict):
    def __missing__(self, key: str) -> str:
        # 缺少变量时原样留下占位符，避免整轮对话因格式化报错而中断。
        return "{" + key + "}"


def render_template(template: str, values: Mapping[str, Any]) -> str:
    """填入已知数据；缺少的字段先原样留着，避免这轮回复直接报错。"""

    return template.format_map(_SafeValues(values))
