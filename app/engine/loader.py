"""读取各业务的配置文件，提前检查配置，并按配置找到要执行的动作。"""

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import yaml

from app.engine.action_result import ActionResult, action_success, validate_action_result
from app.engine.constants import ActionName, OTHER_HANDLER_NAME, RecoveryTrigger
from app.engine.outputs import DialogueOutput, OUTPUT_NAMES
from app.engine.recovery import RecoveryStep, SYSTEM_FALLBACKS
from app.engine.route_tasks import ROUTE_TASKS


class PluginConfigError(RuntimeError):
    pass


class PluginKind(StrEnum):
    WORKFLOW = "workflow"
    OVERLAY = "overlay"


class SwitchPolicy(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class StatePolicy:
    switch_policy: SwitchPolicy = SwitchPolicy.ALLOW
    remind_after_other: bool = False
    reminder: str = ""


@dataclass(frozen=True)
class ErrorTransitionConfig:
    reply: str
    next_state: str
    out: DialogueOutput


@dataclass(frozen=True)
class TransitionConfig:
    state: str
    intent: str
    action: str
    reply: str
    next_state: str
    out: DialogueOutput
    on_error: dict[str, ErrorTransitionConfig]

    def error_transition_for(self, error: str) -> ErrorTransitionConfig | None:
        return self.on_error.get(error)


@dataclass(frozen=True)
class Plugin:
    name: str
    kind: PluginKind
    states: tuple[str, ...]
    terminal_states: frozenset[str]
    prompt: str
    transitions: tuple[TransitionConfig, ...]
    templates: dict[str, str]
    fallbacks: dict[str, str]
    recovery: dict[str, tuple[RecoveryStep, ...]]
    module_prefix: str
    route_task: str
    other_enabled: bool
    state_policies: dict[str, StatePolicy]

    @property
    def initial_state(self) -> str:
        if not self.states:
            raise PluginConfigError(f"无状态插件 {self.name} 没有 initial_state")
        return self.states[0]

    @property
    def is_overlay(self) -> bool:
        return self.kind == PluginKind.OVERLAY

    def transition_for(self, state: str, intent: str) -> TransitionConfig | None:
        return next(
            (item for item in self.transitions if item.state == state and item.intent == intent),
            None,
        )

    def recovery_steps_for(self, trigger: str) -> tuple[RecoveryStep, ...]:
        return self.recovery[trigger]

    def state_policy_for(self, state: str) -> StatePolicy:
        return self.state_policies.get(state, StatePolicy())


class PluginLoader:
    """把业务目录里的 YAML 和提示词整理成引擎可直接使用的插件。"""

    def __init__(self, businesses_dir: Path | None = None) -> None:
        self.businesses_dir = businesses_dir or Path(__file__).parents[1] / "businesses"
        self._plugins: dict[str, Plugin] = {}
        self._route_plugins: dict[str, str] = {}
        self._handlers: dict[tuple[str, str], Callable[[dict[str, Any]], ActionResult]] = {}
        self._lock = RLock()

    def load_all(self, *, force: bool = False) -> dict[str, Plugin]:
        """第一次使用时加载全部插件；只有明确要求时才重新读取。"""

        with self._lock:
            if self._plugins and not force:
                return dict(self._plugins)
            plugins: dict[str, Plugin] = {}
            route_tasks: dict[str, str] = {}
            for config_path in sorted(self.businesses_dir.glob("*/plugin.yaml")):
                plugin = self._load_one(config_path)
                # 名称和顶层任务必须一一对应，否则路由后不知道该选哪个插件。
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
        """读取一个插件，启动前就检查状态、动作、话术是否对得上。"""

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise PluginConfigError(f"无法加载 {path}: {exc}") from exc

        kind_text = str(raw.get("kind", PluginKind.WORKFLOW.value)).strip().lower()
        try:
            kind = PluginKind(kind_text)
        except ValueError as exc:
            raise PluginConfigError(
                f"{path}: kind 必须是 {[item.value for item in PluginKind]} 之一"
            ) from exc

        if kind == PluginKind.OVERLAY:
            # other 只处理这一句，不需要普通业务那套状态和动作表。
            return self._load_overlay(path, raw)

        required = {
            "name",
            "route_task",
            "states",
            "actions",
            "templates",
            "fallbacks",
            "recovery",
        }
        missing = required - raw.keys()
        if missing:
            raise PluginConfigError(f"{path} 缺少字段: {', '.join(sorted(missing))}")
        prompt = self._load_prompt(path, raw)

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
        # 每条规则都由“当前状态 + 用户意图”唯一确定，不能出现两种动作。
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
            output_name = str(config["out"]).strip().upper()
            try:
                output = DialogueOutput[output_name]
            except KeyError as exc:
                raise PluginConfigError(
                    f"{path}: actions[{index}].out 必须是 {sorted(OUTPUT_NAMES)} 之一"
                ) from exc
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
                error_output_name = str(error_config["out"]).strip().upper()
                try:
                    error_output = DialogueOutput[error_output_name]
                except KeyError as exc:
                    raise PluginConfigError(
                        f"{path}: actions[{index}].on_error.{error_code}.out "
                        f"必须是 {sorted(OUTPUT_NAMES)} 之一"
                    ) from exc
                if error_next_state not in states:
                    raise PluginConfigError(
                        f"{path}: actions[{index}].on_error.{error_code} "
                        f"引用了未声明状态 {error_next_state}"
                    )
                on_error[str(error_code)] = ErrorTransitionConfig(
                    reply=str(error_config["reply"]),
                    next_state=error_next_state,
                    out=error_output,
                )
            transitions.append(
                TransitionConfig(
                    state=state,
                    intent=intent,
                    action=str(config["do"]),
                    reply=str(config["reply"]),
                    next_state=next_state,
                    out=output,
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

        # 恢复步骤也在加载时检查，避免聊到一半才发现没有可用话术。
        raw_recovery = raw["recovery"]
        if not isinstance(raw_recovery, dict):
            raise PluginConfigError(f"{path}: recovery 必须是对象")
        required_recovery_triggers = set(RecoveryTrigger)
        missing_recovery_triggers = required_recovery_triggers - raw_recovery.keys()
        if missing_recovery_triggers:
            raise PluginConfigError(
                f"{path}: recovery 缺少类型 {', '.join(sorted(missing_recovery_triggers))}"
            )
        extra_recovery_triggers = raw_recovery.keys() - required_recovery_triggers
        if extra_recovery_triggers:
            raise PluginConfigError(
                f"{path}: recovery 包含未知类型 "
                f"{', '.join(sorted(extra_recovery_triggers))}"
            )
        recovery: dict[str, tuple[RecoveryStep, ...]] = {}
        for trigger in sorted(required_recovery_triggers):
            raw_steps = raw_recovery[trigger]
            if not isinstance(raw_steps, list) or not raw_steps:
                raise PluginConfigError(f"{path}: recovery.{trigger} 必须是非空列表")
            steps: list[RecoveryStep] = []
            for index, raw_step in enumerate(raw_steps):
                if not isinstance(raw_step, dict):
                    raise PluginConfigError(
                        f"{path}: recovery.{trigger}[{index}] 必须是对象"
                    )
                missing_step_fields = {"reply", "out"} - raw_step.keys()
                if missing_step_fields:
                    raise PluginConfigError(
                        f"{path}: recovery.{trigger}[{index}] 缺少字段 "
                        f"{', '.join(sorted(missing_step_fields))}"
                    )
                reply_key = str(raw_step["reply"])
                output_name = str(raw_step["out"]).strip().upper()
                if output_name not in {"CHAT", "HUMAN"}:
                    raise PluginConfigError(
                        f"{path}: recovery.{trigger}[{index}].out 只能是 CHAT 或 HUMAN"
                    )
                output = DialogueOutput[output_name]
                contextual_keys = {
                    f"{state.lower()}_{reply_key}"
                    for state in states
                }
                if (
                    reply_key not in fallbacks
                    and reply_key not in SYSTEM_FALLBACKS
                    and not contextual_keys.intersection(fallbacks)
                ):
                    raise PluginConfigError(
                        f"{path}: recovery.{trigger}[{index}] 引用了未知话术 {reply_key}"
                    )
                if index < len(raw_steps) - 1 and output != DialogueOutput.CHAT:
                    raise PluginConfigError(
                        f"{path}: recovery.{trigger} 只有最后一步可以转人工"
                    )
                steps.append(RecoveryStep(reply_key, output))
            if steps[-1].out != DialogueOutput.HUMAN:
                raise PluginConfigError(
                    f"{path}: recovery.{trigger} 最后一步必须设置 out: HUMAN"
                )
            recovery[trigger] = tuple(steps)

        route_task = str(raw["route_task"]).strip().upper()
        if not route_task:
            raise PluginConfigError(f"{path}: route_task 不能为空")
        if route_task not in ROUTE_TASKS:
            allowed_tasks = ", ".join(sorted(ROUTE_TASKS))
            raise PluginConfigError(
                f"{path}: route_task {route_task} 不在 Router 提示词的 Task 范围内: "
                f"{allowed_tasks}"
            )

        other_enabled, state_policies = self._load_workflow_policies(path, raw, states)

        return Plugin(
            name=str(raw["name"]),
            kind=kind,
            states=states,
            terminal_states=terminal_states,
            prompt=prompt,
            transitions=tuple(transitions),
            templates=templates,
            fallbacks=fallbacks,
            recovery=recovery,
            module_prefix=f"app.businesses.{path.parent.name}",
            route_task=route_task,
            other_enabled=other_enabled,
            state_policies=state_policies,
        )

    def _load_overlay(self, path: Path, raw: dict[str, Any]) -> Plugin:
        required = {"name", "route_task"}
        missing = required - raw.keys()
        if missing:
            raise PluginConfigError(f"{path} 缺少字段: {', '.join(sorted(missing))}")
        prompt = self._load_prompt(path, raw)
        route_task = str(raw["route_task"]).strip().upper()
        if route_task not in ROUTE_TASKS:
            allowed_tasks = ", ".join(sorted(ROUTE_TASKS))
            raise PluginConfigError(
                f"{path}: route_task {route_task} 不在 Router 提示词的 Task 范围内: "
                f"{allowed_tasks}"
            )
        if str(raw.get("external_output", "CHAT")).strip().upper() != "CHAT":
            raise PluginConfigError(f"{path}: overlay 插件 external_output 只能是 CHAT")
        return Plugin(
            name=str(raw["name"]),
            kind=PluginKind.OVERLAY,
            states=(),
            terminal_states=frozenset(),
            prompt=prompt,
            transitions=(),
            templates={},
            fallbacks={},
            recovery={},
            module_prefix=f"app.businesses.{path.parent.name}",
            route_task=route_task,
            other_enabled=False,
            state_policies={},
        )

    @staticmethod
    def _load_prompt(path: Path, raw: dict[str, Any]) -> str:
        """提示词可以写在 YAML 中，也可以单独放文件，但只能选一种。"""

        has_inline = "prompt" in raw
        has_file = "prompt_file" in raw
        if has_inline == has_file:
            raise PluginConfigError(f"{path}: 必须且只能配置 prompt 或 prompt_file")
        if has_inline:
            prompt = str(raw["prompt"])
        else:
            # 只接受同目录文件名，避免误读其他业务或别的路径下的文件。
            file_name = str(raw["prompt_file"]).strip()
            if file_name in {"", ".", ".."} or Path(file_name).name != file_name:
                raise PluginConfigError(f"{path}: prompt_file 必须是插件目录下的文件名")
            prompt_path = path.parent / file_name
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PluginConfigError(f"无法加载 {prompt_path}: {exc}") from exc
        if not prompt.strip():
            raise PluginConfigError(f"{path}: 提示词不能为空")
        return prompt

    @staticmethod
    def _load_workflow_policies(
        path: Path,
        raw: dict[str, Any],
        states: tuple[str, ...],
    ) -> tuple[bool, dict[str, StatePolicy]]:
        """读出每一步能否切换业务，以及回答插话后要不要提醒用户。"""

        raw_handlers = raw.get("global_handlers", {})
        if not isinstance(raw_handlers, dict):
            raise PluginConfigError(f"{path}: global_handlers 必须是对象")
        raw_other = raw_handlers.get(OTHER_HANDLER_NAME, {})
        if not isinstance(raw_other, dict):
            raise PluginConfigError(f"{path}: global_handlers.other 必须是对象")
        other_enabled = bool(raw_other.get("enabled", True))

        raw_policies = raw.get("state_policies", {})
        if not isinstance(raw_policies, dict):
            raise PluginConfigError(f"{path}: state_policies 必须是对象")
        unknown_states = set(raw_policies) - set(states)
        if unknown_states:
            raise PluginConfigError(
                f"{path}: state_policies 引用了未声明状态 {sorted(unknown_states)}"
            )
        policies: dict[str, StatePolicy] = {}
        for state in states:
            config = raw_policies.get(state, {})
            if not isinstance(config, dict):
                raise PluginConfigError(f"{path}: state_policies.{state} 必须是对象")
            policy_text = str(config.get("switch_policy", "allow")).strip().lower()
            try:
                switch_policy = SwitchPolicy(policy_text)
            except ValueError as exc:
                raise PluginConfigError(
                    f"{path}: state_policies.{state}.switch_policy 必须是 "
                    "allow、confirm 或 deny"
                ) from exc
            remind = bool(config.get("remind_after_other", False))
            reminder = str(config.get("reminder", "")).strip()
            if remind and not reminder:
                raise PluginConfigError(
                    f"{path}: state_policies.{state} 开启提醒时必须配置 reminder"
                )
            policies[state] = StatePolicy(switch_policy, remind, reminder)
        return other_enabled, policies

    @property
    def plugins(self) -> dict[str, Plugin]:
        return self.load_all()

    def get(self, name: str) -> Plugin:
        try:
            return self.plugins[name]
        except KeyError as exc:
            raise PluginConfigError(f"未知业务插件: {name}") from exc

    def get_by_route_task(self, task: str) -> Plugin:
        """模型选了哪项业务，就查那项业务对应的插件。"""

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
        """找到配置里写的动作函数，执行一次并检查它的返回值。"""

        if action == ActionName.NONE:
            return action_success()
        plugin = self.get(business)
        cache_key = (business, action)
        handler = self._handlers.get(cache_key)
        if handler is None:
            # 第一次用到动作时再导入；以后直接复用已找到的函数。
            module = import_module(f"{plugin.module_prefix}.handlers")
            handler = getattr(module, action)
            self._handlers[cache_key] = handler

        # 这里才真正调用业务代码，前面的步骤只是在选动作。
        result = handler(state)
        return validate_action_result(result, f"{business}.{action}")


plugin_loader = PluginLoader()
