"""显式场景登记及配置校验；各场景独立保存，不继承别的场景话术。"""

from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from types import MappingProxyType
from typing import Mapping

import yaml

SCENARIO_IDS = ("charge_dispute", "refund_progress", "scan_confusion")
SCENARIO_ROOT = Path(__file__).parent / "scenarios"
FACT_FIELDS = frozenset({
    "order_no", "product", "amount", "pay_status", "pay_time", "pay_method",
    "subscription_status", "subscription_time", "subscription_channel", "scan_relation",
    "refund_status", "refund_channel", "refund_request_time", "refund_time", "refund_fail_reason",
})


@dataclass(frozen=True)
class Scenario:
    name: str
    defaults: Mapping[str, str]
    replies: Mapping[str, str]
    timeframe: str
    prompt: str

    def explain(self, order: Mapping) -> tuple[str, dict[str, str]]:
        facts = {field: str(order[field]) if order.get(field) is not None and order[field] != ""
                 else default for field, default in self.defaults.items()}
        values = {**facts, "timeframe": self.timeframe}
        text = "".join(self.replies[key].format_map(values)
                       for key in ("summary", "arrangement", "apology", "question"))
        return text, facts


def load_scenario(name: str, root: Path = SCENARIO_ROOT) -> Scenario:
    if name not in SCENARIO_IDS:
        raise ValueError(f"未知核查场景：{name}")
    path = root / name
    config = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
    replies = yaml.safe_load((path / "replies.yaml").read_text(encoding="utf-8"))
    prompt = (path / "prompt.md").read_text(encoding="utf-8").strip()
    if not isinstance(config, dict) or config.get("id") != name:
        raise ValueError(f"{name}: 场景 ID 不匹配")
    defaults = config.get("facts")
    timeframe = config.get("timeframe")
    if (not isinstance(defaults, dict) or not defaults or set(defaults) - FACT_FIELDS
            or not all(isinstance(v, str) and v for v in defaults.values())):
        raise ValueError(f"{name}: facts 字段配置无效")
    if not isinstance(timeframe, str) or not timeframe.strip() or not prompt:
        raise ValueError(f"{name}: 时效说明或场景提示为空")
    if not isinstance(replies, dict) or set(replies) != {"summary", "arrangement", "apology", "question"}:
        raise ValueError(f"{name}: 话术必须包含 summary/arrangement/apology/question")
    for text in replies.values():
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name}: 话术为空")
        for _, field, spec, conversion in Formatter().parse(text):
            if field is not None and (field not in set(defaults) | {"timeframe"} or spec or conversion):
                raise ValueError(f"{name}: 不支持的话术变量 {field}")
    return Scenario(name, MappingProxyType(defaults), MappingProxyType(replies), timeframe, prompt)
