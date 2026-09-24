"""发现、校验并加载业务插件 YAML 与处理函数。"""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import yaml


class PluginConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class IntentConfig:
    name: str
    keywords: tuple[str, ...]
    action: str
    slot_patterns: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionConfig:
    name: str
    handler: str
    required_slots: tuple[str, ...] = ()
    success_template: str = "success"


@dataclass(frozen=True)
class Plugin:
    name: str
    description: str
    keywords: tuple[str, ...]
    default_intent: str
    intents: dict[str, IntentConfig]
    actions: dict[str, ActionConfig]
    templates: dict[str, str]
    module_prefix: str


class PluginLoader:
    def __init__(self, businesses_dir: Path | None = None) -> None:
        self.businesses_dir = businesses_dir or Path(__file__).parents[1] / "businesses"
        self._plugins: dict[str, Plugin] = {}
        self._handlers: dict[tuple[str, str], Callable] = {}
        self._lock = RLock()

    def load_all(self, *, force: bool = False) -> dict[str, Plugin]:
        with self._lock:
            if self._plugins and not force:
                return dict(self._plugins)
            plugins: dict[str, Plugin] = {}
            for config_path in sorted(self.businesses_dir.glob("*/plugin.yaml")):
                plugin = self._load_one(config_path)
                if plugin.name in plugins:
                    raise PluginConfigError(f"业务插件重名: {plugin.name}")
                plugins[plugin.name] = plugin
            if "chat" not in plugins:
                raise PluginConfigError("必须提供 chat 兜底插件")
            self._plugins = plugins
            self._handlers.clear()
            return dict(plugins)

    def _load_one(self, path: Path) -> Plugin:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise PluginConfigError(f"无法加载 {path}: {exc}") from exc

        required = {"name", "intents", "actions", "templates"}
        missing = required - raw.keys()
        if missing:
            raise PluginConfigError(f"{path} 缺少字段: {', '.join(sorted(missing))}")

        intents = {
            name: IntentConfig(
                name=name,
                keywords=tuple(config.get("keywords", [])),
                action=config["action"],
                slot_patterns=dict(config.get("slot_patterns", {})),
            )
            for name, config in raw["intents"].items()
        }
        actions = {
            name: ActionConfig(
                name=name,
                handler=config["handler"],
                required_slots=tuple(config.get("required_slots", [])),
                success_template=config.get("success_template", "success"),
            )
            for name, config in raw["actions"].items()
        }
        for intent in intents.values():
            if intent.action not in actions:
                raise PluginConfigError(f"{path}: intent {intent.name} 引用了未知 action {intent.action}")

        return Plugin(
            name=str(raw["name"]),
            description=str(raw.get("description", "")),
            keywords=tuple(raw.get("keywords", [])),
            default_intent=str(raw.get("default_intent") or next(iter(intents))),
            intents=intents,
            actions=actions,
            templates={str(key): str(value) for key, value in raw["templates"].items()},
            module_prefix=f"app.businesses.{path.parent.name}",
        )

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self.load_all()

    def get(self, name: str) -> Plugin:
        try:
            return self.plugins[name]
        except KeyError as exc:
            raise PluginConfigError(f"未知业务插件: {name}") from exc

    def execute(self, business: str, action: str, state: dict[str, Any]) -> dict[str, Any]:
        plugin = self.get(business)
        action_config = plugin.actions[action]
        cache_key = (business, action)
        handler = self._handlers.get(cache_key)
        if handler is None:
            module_name, function_name = action_config.handler.rsplit(".", 1)
            module = import_module(f"{plugin.module_prefix}.{module_name}")
            handler = getattr(module, function_name)
            self._handlers[cache_key] = handler
        result = handler(state)
        if not isinstance(result, dict):
            raise TypeError(f"{business}.{action} 必须返回 dict")
        return result


plugin_loader = PluginLoader()
