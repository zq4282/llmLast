"""业务话术模板渲染。"""

from typing import Any, Mapping


class _SafeValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, values: Mapping[str, Any]) -> str:
    """缺少非关键字段时保留占位符，避免模板错误击穿接口。"""

    return template.format_map(_SafeValues(values))
