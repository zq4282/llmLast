"""一轮业务处理分成五步：选业务、听懂、定动作、执行、组织回复。"""

from dataclasses import dataclass
from typing import Any

from app.engine.action_result import action_failure
from app.engine.constants import (
    ActionName,
    ActionResultField,
    ChatField,
    EngineIntent,
    FallbackKey,
    HandoffReason,
    INTERNAL_ACTION_ERROR,
    RecoveryTrigger,
    SYSTEM_BUSINESS,
)
from app.engine.llm import IntentLLM, intent_llm
from app.engine.loader import Plugin, PluginConfigError, PluginLoader, plugin_loader
from app.engine.outputs import DialogueOutput
from app.engine.recovery import SYSTEM_FALLBACKS, recovery_decision
from app.engine.render import render_template
from app.engine.route_tasks import ROUTE_TASK_UNKNOWN
from app.engine.state import ChatState, ReplySource


@dataclass(frozen=True)
class RouterNode:
    loader: PluginLoader
    llm: IntentLLM

    def select_plugin(self, state: ChatState) -> Plugin | None:
        """未锁定业务时，选出固定业务插件；未知任务交给 other。"""

        decision = self.llm.classify_route(
            state[ChatField.MESSAGE], state.get(ChatField.HISTORY, [])
        )
        if decision.task == ROUTE_TASK_UNKNOWN:
            return None
        try:
            plugin = self.loader.get_by_route_task(decision.task)
        except PluginConfigError:
            return None
        return None if plugin.is_overlay else plugin

    def __call__(self, state: ChatState) -> dict[str, Any]:
        """已经在办某项业务就接着办；没有业务时才请模型重新选择。"""

        existing_business = state.get(ChatField.BUSINESS)
        if existing_business and existing_business in self.loader.plugins:
            plugin = self.loader.get(existing_business)
            return {
                ChatField.BUSINESS: plugin.name,
                ChatField.SKIP_UNDERSTANDING: bool(state.get(ChatField.SKIP_UNDERSTANDING, False)),
            }

        plugin = self.select_plugin(state)
        if plugin is None:
            return {
                ChatField.BUSINESS: state.get(ChatField.BUSINESS) or SYSTEM_BUSINESS,
                ChatField.INTENT: EngineIntent.UNKNOWN,
                ChatField.SLOTS: {},
                ChatField.SKIP_UNDERSTANDING: True,
            }
        return {
            ChatField.BUSINESS: plugin.name,
            ChatField.SKIP_UNDERSTANDING: False,
        }


@dataclass(frozen=True)
class UnderstandNode:
    loader: PluginLoader
    llm: IntentLLM

    def __call__(self, state: ChatState) -> dict[str, Any]:
        """按当前业务的提示词理解用户这句话，并提取订单号等信息。"""

        if state.get(ChatField.SKIP_UNDERSTANDING):
            # 意图已由运行时指定，例如转人工，不必再请求模型。
            return {}

        plugin = self.loader.get(state[ChatField.BUSINESS])
        plugin_state = state.get(ChatField.PLUGIN_STATE)
        if plugin_state not in plugin.states:
            plugin_state = plugin.initial_state
        context = dict(state.get(ChatField.CONTEXT, {}))
        decision = self.llm.understand(
            state[ChatField.MESSAGE],
            plugin,
            plugin_state,
            context,
            state.get(ChatField.HISTORY, []),
        )
        return {
            ChatField.PLUGIN_STATE: plugin_state,
            ChatField.INTENT: decision.intent,
            ChatField.SLOTS: decision.slots,
        }


@dataclass(frozen=True)
class DecisionNode:
    loader: PluginLoader

    def _use_plugin_recovery_reply(
        self,
        state: ChatState,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """同样是没听懂，优先说当前业务准备的那句更贴切的话。"""

        business = state.get(ChatField.BUSINESS)
        if not business or business not in self.loader.plugins:
            return decision
        plugin = self.loader.get(business)
        plugin_state = state.get(ChatField.PLUGIN_STATE)
        if plugin_state not in plugin.states:
            plugin_state = plugin.initial_state
        contextual_key = f"{plugin_state.lower()}_{decision[ChatField.REPLY_KEY]}"
        if contextual_key in plugin.fallbacks:
            reply_key = contextual_key
        elif decision[ChatField.REPLY_KEY] in plugin.fallbacks:
            reply_key = decision[ChatField.REPLY_KEY]
        else:
            return decision
        return {
            **decision,
            ChatField.REPLY_KEY: reply_key,
            ChatField.REPLY_SOURCE: ReplySource.PLUGIN_FALLBACK,
        }

    def _recovery_for_state(
        self,
        state: ChatState,
        *,
        trigger: str | None = None,
    ) -> dict[str, Any] | None:
        """读取插件恢复步骤；没有已选插件时使用引擎默认步骤。"""

        resolved_trigger = trigger or (
            RecoveryTrigger.UNKNOWN if state.get(ChatField.INTENT) == EngineIntent.UNKNOWN else None
        )
        steps = None
        business = state.get(ChatField.BUSINESS)
        if resolved_trigger and business and business in self.loader.plugins:
            steps = self.loader.get(business).recovery_steps_for(resolved_trigger)
        decision = recovery_decision(dict(state), trigger=trigger, steps=steps)
        return self._use_plugin_recovery_reply(state, decision) if decision is not None else None

    def __call__(self, state: ChatState) -> dict[str, Any]:
        """用“办到哪一步 + 这句话的意思”查出接下来该做什么。"""

        recovery = self._recovery_for_state(state)
        if recovery is not None:
            return recovery

        plugin = self.loader.get(state[ChatField.BUSINESS])
        plugin_state = state.get(ChatField.PLUGIN_STATE, plugin.initial_state)
        if plugin_state in plugin.terminal_states:
            return {
                ChatField.ACTION: ActionName.NONE,
                ChatField.REPLY_KEY: FallbackKey.END,
                ChatField.NEXT_PLUGIN_STATE: plugin_state,
                ChatField.OUT: DialogueOutput.END,
                ChatField.REPLY_SOURCE: ReplySource.PLUGIN_FALLBACK,
                ChatField.UNRECOGNIZED_COUNT: 0,
                ChatField.HANDOFF_REASON: None,
            }

        transition = plugin.transition_for(plugin_state, state[ChatField.INTENT])
        if transition is None:
            # 当前业务没有这条走法时，保持原进度并逐次提示，避免一直重复同一句话。
            unsupported = self._recovery_for_state(state, trigger=RecoveryTrigger.UNSUPPORTED)
            assert unsupported is not None
            return unsupported
        return {
            ChatField.ACTION: transition.action,
            ChatField.REPLY_KEY: transition.reply,
            ChatField.NEXT_PLUGIN_STATE: transition.next_state,
            ChatField.OUT: transition.out,
            ChatField.REPLY_SOURCE: ReplySource.TEMPLATE,
            ChatField.UNRECOGNIZED_COUNT: 0,
            ChatField.HANDOFF_REASON: (
                HandoffReason.USER_REQUESTED if transition.out == DialogueOutput.HUMAN else None
            ),
        }


@dataclass(frozen=True)
class ActionNode:
    loader: PluginLoader

    def __call__(self, state: ChatState) -> dict[str, Any]:
        """执行上一步选中的一个业务动作。"""

        try:
            result = self.loader.invoke_action_handler(
                state[ChatField.BUSINESS],
                state.get(ChatField.ACTION, ActionName.NONE),
                dict(state),
            )
            return {ChatField.ACTION_RESULT: result}
        except Exception:
            # 动作内部出错时返回失败结果，让回复节点按业务话术收尾。
            return {ChatField.ACTION_RESULT: action_failure(INTERNAL_ACTION_ERROR)}


@dataclass(frozen=True)
class ReplyNode:
    loader: PluginLoader

    def __call__(self, state: ChatState) -> dict[str, Any]:
        """挑选合适的话术，填入动作结果，再决定业务下一步停在哪里。"""

        result = state.get(ChatField.ACTION_RESULT, {})
        reply_source = state.get(ChatField.REPLY_SOURCE, ReplySource.TEMPLATE)
        plugin = (
            None
            if reply_source == ReplySource.SYSTEM_FALLBACK
            else self.loader.get(state[ChatField.BUSINESS])
        )
        transition = (
            plugin.transition_for(
                state.get(ChatField.PLUGIN_STATE, plugin.initial_state),
                state.get(ChatField.INTENT, EngineIntent.UNKNOWN),
            )
            if plugin
            else None
        )
        if not result.get(ActionResultField.OK, True):
            # 动作失败时先找该动作专门配置的错误处理，例如查不到订单就追问号码。
            error_code = str(result.get(ActionResultField.ERROR, FallbackKey.UNKNOWN))
            error_transition = transition.error_transition_for(error_code) if transition else None
            if error_transition:
                template = plugin.templates.get(
                    error_transition.reply,
                    plugin.fallbacks.get(
                        error_transition.reply,
                        plugin.fallbacks.get(FallbackKey.UNKNOWN, ""),
                    ),
                )
                next_plugin_state = error_transition.next_state
                out = error_transition.out
            else:
                # 当前 action 未声明该错误码时，才进入插件级全局兜底。
                assert plugin is not None
                template = plugin.fallbacks.get(
                    error_code,
                    plugin.fallbacks.get(FallbackKey.UNKNOWN, "处理失败，请重试。"),
                )
                next_plugin_state = state.get(ChatField.PLUGIN_STATE, plugin.initial_state)
                out = DialogueOutput.CHAT
        else:
            # 动作成功时，按决策结果使用正常话术或对应的兜底话术。
            reply_key = state.get(ChatField.REPLY_KEY, FallbackKey.UNKNOWN)
            if reply_source == ReplySource.SYSTEM_FALLBACK:
                template = SYSTEM_FALLBACKS.get(
                    reply_key, SYSTEM_FALLBACKS[FallbackKey.UNKNOWN_FIRST]
                )
            elif reply_source == ReplySource.PLUGIN_FALLBACK:
                assert plugin is not None
                template = plugin.fallbacks.get(
                    reply_key, plugin.fallbacks.get(FallbackKey.UNKNOWN, "")
                )
            else:
                assert plugin is not None
                template = plugin.templates.get(
                    reply_key, plugin.fallbacks.get(FallbackKey.UNKNOWN, "")
                )
            default_plugin_state = (
                plugin.initial_state if plugin else state.get(ChatField.PLUGIN_STATE)
            )
            next_plugin_state = state.get(
                ChatField.NEXT_PLUGIN_STATE,
                state.get(ChatField.PLUGIN_STATE, default_plugin_state),
            )
            out: DialogueOutput = state.get(ChatField.OUT, DialogueOutput.CHAT)
        values = {**state.get(ChatField.CONTEXT, {}), **state.get(ChatField.SLOTS, {}), **result}
        if result.get(ActionResultField.OK, True):
            # 成功才把这轮提取的信息和动作结果留给后续对话。
            context = {
                key: value
                for key, value in values.items()
                if key not in {ActionResultField.OK, ActionResultField.ERROR}
            }
        else:
            # 失败时仍用旧信息；这轮错误的订单号等内容不能覆盖已确认的数据。
            context = dict(state.get(ChatField.CONTEXT, {}))
        handoff_reason = None
        if out == DialogueOutput.HUMAN:
            handoff_reason = state.get(ChatField.HANDOFF_REASON)
            if not result.get(ActionResultField.OK, True):
                # 动作失败后按配置转人工，要说明是动作失败，不是用户主动要求。
                handoff_reason = HandoffReason.ACTION_FAILED
        return {
            ChatField.REPLY: render_template(template, values),
            ChatField.PLUGIN_STATE: next_plugin_state,
            ChatField.CONTEXT: context,
            ChatField.OUT: out,
            ChatField.UNRECOGNIZED_COUNT: state.get(ChatField.UNRECOGNIZED_COUNT, 0),
            ChatField.HANDOFF_REASON: handoff_reason,
        }


UNDERSTAND_NODE = UnderstandNode(plugin_loader, intent_llm)
DECISION_NODE = DecisionNode(plugin_loader)
ACTION_NODE = ActionNode(plugin_loader)
REPLY_NODE = ReplyNode(plugin_loader)


def router(state: ChatState, *, loader: PluginLoader | None = None) -> dict[str, Any]:
    return RouterNode(loader or plugin_loader, intent_llm)(state)


def understand(state: ChatState) -> dict[str, Any]:
    return UNDERSTAND_NODE(state)


def decide(state: ChatState) -> dict[str, Any]:
    return DECISION_NODE(state)


def run_action(state: ChatState) -> dict[str, Any]:
    return ACTION_NODE(state)


def reply(state: ChatState) -> dict[str, Any]:
    return REPLY_NODE(state)


__all__ = [
    "RouterNode", "UnderstandNode", "DecisionNode", "ActionNode", "ReplyNode",
    "router", "understand", "decide", "run_action", ChatField.REPLY,
]
