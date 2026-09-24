"""发现、校验并加载状态机业务插件。"""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import yaml

from app.engine.action_result import ActionResult, action_success, validate_action_result
from app.engine.route_tasks import ROUTE_TASKS


class PluginConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ErrorTransitionConfig:
    reply: str
    next_state: str
    out: str


@dataclass(frozen=True)
class TransitionConfig:
    state: str
    intent: str
    action: str
    reply: str
    next_state: str
    out: str
    on_error: dict[str, ErrorTransitionConfig]

    def error_transition_for(self, error: str) -> ErrorTransitionConfig | None:
        return self.on_error.get(error)


@dataclass(frozen=True)
class Plugin:
    name: str
    states: tuple[str, ...]
    terminal_states: frozenset[str]
    prompt: str
    transitions: tuple[TransitionConfig, ...]
    templates: dict[str, str]
    fallbacks: dict[str, str]
    module_prefix: str
    route_task: str

    @property
    def initial_state(self) -> str:
        return self.states[0]

    def transition_for(self, state: str, intent: str) -> TransitionConfig | None:
        return next(
            (item for item in self.transitions if item.state == state and item.intent == intent),
            None,
        )


class PluginLoader:
    def __init__(self, businesses_dir: Path | None = None) -> None:
        self.businesses_dir = businesses_dir or Path(__file__).parents[1] / "businesses"
        self._plugins: dict[str, Plugin] = {}
        self._route_plugins: dict[str, str] = {}
        self._handlers: dict[tuple[str, str], Callable[[dict[str, Any]], ActionResult]] = {}
        self._lock = RLock()

    def load_all(self, *, force: bool = False) -> dict[str, Plugin]:
        with self._lock:
            if self._plugins and not force:
                return dict(self._plugins)
            plugins: dict[str, Plugin] = {}
            route_tasks: dict[str, str] = {}
            for config_path in sorted(self.businesses_dir.glob("*/plugin.yaml")):
                plugin = self._load_one(config_path)
                if plugin.name in plugins:
                    raise PluginConfigError(f"业务插件重名: {plugin.name}")
                previous = route_tasks.get(plugin.route_task)
                if previous:
                    raise PluginConfigError(
                        f"Task {plugin.route_task} 同时映射了插件 {previous} 和 {plugin.name}"
                    )
                route_tasks[plugin.route_task] = plugin.name
                plugins[plugin.name] = plugin
            if not plugins:
                raise PluginConfigError("至少需要一个业务插件")
            self._plugins = plugins
            self._route_plugins = route_tasks
            self._handlers.clear()
            return dict(plugins)

    def _load_one(self, path: Path) -> Plugin:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise PluginConfigError(f"无法加载 {path}: {exc}") from exc

        required = {
            "name",
            "route_task",
            "states",
            "prompt",
            "actions",
            "templates",
            "fallbacks",
        }
        missing = required - raw.keys()
        if missing:
            raise PluginConfigError(f"{path} 缺少字段: {', '.join(sorted(missing))}")

        states = tuple(str(item) for item in raw["states"])
        if not states:
            raise PluginConfigError(f"{path}: states 不能为空")
        if len(states) != len(set(states)):
            raise PluginConfigError(f"{path}: states 存在重复值")

        terminal_states = frozenset(str(item) for item in raw.get("terminal_states", []))
        unknown_terminal_states = terminal_states - set(states)
        if unknown_terminal_states:
            raise PluginConfigError(
                f"{path}: terminal_states 引用了未声明状态 {sorted(unknown_terminal_states)}"
            )

        transitions: list[TransitionConfig] = []
        transition_keys: set[tuple[str, str]] = set()
        for index, config in enumerate(raw["actions"]):
            required_fields = {"state", "intent", "do", "reply", "next", "out"}
            missing_fields = required_fields - config.keys()
            if missing_fields:
                raise PluginConfigError(
                    f"{path}: actions[{index}] 缺少字段 {', '.join(sorted(missing_fields))}"
                )
            state = str(config["state"])
            next_state = str(config["next"])
            intent = str(config["intent"])
            if state not in states or next_state not in states:
                raise PluginConfigError(f"{path}: 动作 {state}/{intent} 引用了未声明状态")
            key = (state, intent)
            if key in transition_keys:
                raise PluginConfigError(f"{path}: 重复动作 {state}/{intent}")
            transition_keys.add(key)
            on_error: dict[str, ErrorTransitionConfig] = {}
            raw_on_error = config.get("on_error", {})
            if not isinstance(raw_on_error, dict):
                raise PluginConfigError(f"{path}: actions[{index}].on_error 必须是对象")
            for error_code, error_config in raw_on_error.items():
                if not isinstance(error_config, dict):
                    raise PluginConfigError(
                        f"{path}: actions[{index}].on_error.{error_code} 必须是对象"
                    )
                required_error_fields = {"reply", "next", "out"}
                missing_error_fields = required_error_fields - error_config.keys()
                if missing_error_fields:
                    raise PluginConfigError(
                        f"{path}: actions[{index}].on_error.{error_code} 缺少字段 "
                        f"{', '.join(sorted(missing_error_fields))}"
                    )
                error_next_state = str(error_config["next"])
                if error_next_state not in states:
                    raise PluginConfigError(
                        f"{path}: actions[{index}].on_error.{error_code} "
                        f"引用了未声明状态 {error_next_state}"
                    )
                on_error[str(error_code)] = ErrorTransitionConfig(
                    reply=str(error_config["reply"]),
                    next_state=error_next_state,
                    out=str(error_config["out"]),
                )
            transitions.append(
                TransitionConfig(
                    state=state,
                    intent=intent,
                    action=str(config["do"]),
                    reply=str(config["reply"]),
                    next_state=next_state,
                    out=str(config["out"]),
                    on_error=on_error,
                )
            )

        templates = {str(key): str(value) for key, value in raw["templates"].items()}
        fallbacks = {str(key): str(value) for key, value in raw["fallbacks"].items()}
        for transition in transitions:
            if transition.reply not in templates and transition.reply not in fallbacks:
                raise PluginConfigError(
                    f"{path}: 动作 {transition.state}/{transition.intent} 引用了未知话术 {transition.reply}"
                )
            for error_code, error_transition in transition.on_error.items():
                if error_transition.reply not in templates and error_transition.reply not in fallbacks:
                    raise PluginConfigError(
                        f"{path}: 动作 {transition.state}/{transition.intent} 的错误 "
                        f"{error_code} 引用了未知话术 {error_transition.reply}"
                    )

        route_task = str(raw["route_task"]).strip().upper()
        if not route_task:
            raise PluginConfigError(f"{path}: route_task 不能为空")
        if route_task not in ROUTE_TASKS:
            allowed_tasks = ", ".join(sorted(ROUTE_TASKS))
            raise PluginConfigError(
                f"{path}: route_task {route_task} 不在 Router 提示词的 Task 范围内: "
                f"{allowed_tasks}"
            )

        return Plugin(
            name=str(raw["name"]),
            states=states,
            terminal_states=terminal_states,
            prompt=str(raw["prompt"]),
            transitions=tuple(transitions),
            templates=templates,
            fallbacks=fallbacks,
            module_prefix=f"app.businesses.{path.parent.name}",
            route_task=route_task,
        )

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self.load_all()

    def get(self, name: str) -> Plugin:
        try:
            return self.plugins[name]
        except KeyError as exc:
            raise PluginConfigError(f"未知业务插件: {name}") from exc

    def get_by_route_task(self, task: str) -> Plugin:
        """只按模型返回的 Task 映射插件，不在程序中重新猜测意图。"""

        normalized_task = task.strip().upper()
        plugins = self.plugins
        plugin_name = self._route_plugins.get(normalized_task)
        if plugin_name:
            return plugins[plugin_name]
        raise PluginConfigError(f"没有可处理 Task {normalized_task} 的插件")

    def invoke_action_handler(
        self,
        business: str,
        action: str,
        state: dict[str, Any],
    ) -> ActionResult:
        """查找并调用 actions.do 指定的业务函数，只执行一个 action。"""

        if action == "none":
            return action_success()
        plugin = self.get(business)
        cache_key = (business, action)
        handler = self._handlers.get(cache_key)
        if handler is None:
            module = import_module(f"{plugin.module_prefix}.handlers")
            handler = getattr(module, action)
            self._handlers[cache_key] = handler

        # 真正执行 app.businesses.<业务>.handlers 中业务函数的位置。
        result = handler(state)
        return validate_action_result(result, f"{business}.{action}")


plugin_loader = PluginLoader()
