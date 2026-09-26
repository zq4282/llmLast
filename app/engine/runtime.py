"""把一轮用户消息送进正确的业务，并处理闲聊、转人工和业务切换。"""

from copy import deepcopy
from typing import Any, Callable

from app.engine.action_result import action_success
from app.engine.constants import (
    ActionName,
    ChatField,
    ConfigField,
    DEFAULT_MAX_REPLY_LEN,
    DEFAULT_SYSTEM_PROMPT,
    EngineIntent,
    FlowField,
    MAX_INTERNAL_REROUTES,
    OTHER_BUSINESS,
    OtherDecisionType,
    OtherIntent,
    PendingSwitchField,
    SwitchDecisionType,
)
from app.engine.flows import FlowManager
from app.engine.graph import WorkflowGraph, get_graph
from app.engine.llm import IntentLLM, intent_llm
from app.engine.loader import PluginLoader, SwitchPolicy, plugin_loader
from app.engine.nodes import RouterNode
from app.engine.outputs import DialogueOutput
from app.engine.render import render_template
from app.engine.route_tasks import (
    ROUTE_TASK_DEFINITIONS,
    ROUTE_TASK_HUMAN,
    ROUTE_TASK_LABELS,
    ROUTE_TASK_UNKNOWN,
)
from app.engine.state import ChatState


class DialogueEngine:
    """安排这一轮该走哪条业务流程；流程进度交给 FlowManager 保存。"""

    def __init__(
        self,
        loader: PluginLoader = plugin_loader,
        llm: IntentLLM = intent_llm,
        graph_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.loader = loader
        self.llm = llm
        self.router = RouterNode(loader, llm)
        # 正常请求使用默认构建入口；传入测试模型时用该模型编译流程图。
        self.graph_factory = graph_factory or (
            get_graph
            if loader is plugin_loader and llm is intent_llm
            else WorkflowGraph(loader, llm).compile
        )

    def run(self, state: ChatState) -> ChatState:
        """先处理等用户确认的切换，再继续已有业务；没有业务才重新路由。"""

        self.loader.load_all()
        working: ChatState = dict(state)
        flows = FlowManager(working, self.loader)
        pending_switch = deepcopy(working.get(ChatField.PENDING_SWITCH))
        if pending_switch:
            return self._handle_pending_switch(working, flows, pending_switch)

        active = flows.active()
        if active is not None:
            plugin = self.loader.get(str(active[FlowField.BUSINESS]))
            if str(active.get(FlowField.PLUGIN_STATE)) in plugin.terminal_states:
                # 旧会话如果还指着已结束的业务，先关掉它再判断这轮新消息。
                flows.complete(active)
            else:
                return self._process_workflow(working, flows, plugin.name)

        plugin = self.router.select_plugin(working)
        if plugin is None:
            return self._process_other(working, flows)

        flows.activate(plugin.name)
        return self._process_workflow(working, flows, plugin.name)

    def _process_workflow(
        self,
        state: ChatState,
        flows: FlowManager,
        business: str,
        *,
        forced_intent: str | None = None,
    ) -> ChatState:
        """从保存的进度开始办当前业务，再把结果交回流程管理器。"""

        flow = flows.active()
        if flow is None:
            raise RuntimeError("ACTIVE 流程不存在")
        plugin = self.loader.get(business)
        working: ChatState = {
            **state,
            ChatField.BUSINESS: business,
            ChatField.PLUGIN_STATE: flow.get(FlowField.PLUGIN_STATE) or plugin.initial_state,
            ChatField.CONTEXT: deepcopy(flow.get(FlowField.CONTEXT, {})),
            ChatField.UNRECOGNIZED_COUNT: int(flow.get(FlowField.UNRECOGNIZED_COUNT, 0)),
            ChatField.COMPLETED_ACTIONS: deepcopy(flow.get(FlowField.COMPLETED_ACTIONS, [])),
            ChatField.SKIP_UNDERSTANDING: forced_intent is not None,
        }
        if forced_intent:
            # 例如用户明确要求人工：直接交给当前业务处理，不再让模型猜一次。
            working.update({ChatField.INTENT: forced_intent, ChatField.SLOTS: {}})

        # 识别为 other 时图只跑到“理解”，此时还没有执行原业务动作。
        working = self.graph_factory().invoke(working)
        if (
            working.get(ChatField.INTENT) == EngineIntent.OTHER
            and plugin.other_enabled
            and int(working.get(ChatField.REROUTE_COUNT, 0)) < MAX_INTERNAL_REROUTES
        ):
            return self._process_other(working, flows)
        return flows.commit(working)

    def _process_other(self, state: ChatState, flows: FlowManager) -> ChatState:
        """让 other 回答插话，或把本轮消息转到另一条固定业务。"""

        other = self.loader.get_by_route_task(ROUTE_TASK_UNKNOWN)
        available_tasks = {
            plugin.route_task: ROUTE_TASK_DEFINITIONS[plugin.route_task]
            for plugin in self.loader.plugins.values()
            if not plugin.is_overlay and plugin.route_task in ROUTE_TASK_DEFINITIONS
        }
        config = state.get(ChatField.CONFIG, {})
        decision = self.llm.handle_other(
            state[ChatField.MESSAGE],
            other,
            available_tasks,
            system_prompt=str(state.get(ChatField.SYSTEM_PROMPT) or DEFAULT_SYSTEM_PROMPT),
            max_reply_len=int(config.get(ConfigField.MAX_REPLY_LEN, DEFAULT_MAX_REPLY_LEN)),
        )
        active = flows.active()
        if decision.decision == OtherDecisionType.ANSWER:
            # 闲聊只回答这一句，不推进原业务；是否提醒用户继续由原业务配置决定。
            reply_text = decision.reply or ""
            if active is not None:
                plugin = self.loader.get(str(active[FlowField.BUSINESS]))
                policy = plugin.state_policy_for(str(active.get(FlowField.PLUGIN_STATE)))
                if policy.remind_after_other:
                    reminder = render_template(policy.reminder, active.get(FlowField.CONTEXT, {}))
                    reply_text = f"{reply_text}{reminder}"
            return self._chat_result(
                state,
                flows,
                reply=reply_text,
                intent=decision.intent or OtherIntent.CHITCHAT,
                business=str(active[FlowField.BUSINESS]) if active else other.name,
            )

        # 一轮只允许二次分流一次，防止两个插件来回转而不回复用户。
        if int(state.get(ChatField.REROUTE_COUNT, 0)) >= MAX_INTERNAL_REROUTES:
            return self._chat_result(
                state,
                flows,
                reply="当前消息暂时无法继续转交，请直接说明需要办理的业务。",
                intent=EngineIntent.UNKNOWN,
                business=str(active[FlowField.BUSINESS]) if active else other.name,
            )

        target = self.loader.get_by_route_task(decision.target_task or "")
        rerouted_state: ChatState = {**state, ChatField.REROUTE_COUNT: MAX_INTERNAL_REROUTES}
        if active is None:
            flows.activate(target.name)
            return self._process_workflow(rerouted_state, flows, target.name)

        current_business = str(active[FlowField.BUSINESS])
        if target.name == current_business:
            return self._process_workflow(rerouted_state, flows, current_business)

        if target.route_task == ROUTE_TASK_HUMAN:
            # 已在业务中要求人工时，仍由当前业务给出转接结果。
            return self._process_workflow(
                rerouted_state, flows, current_business, forced_intent=EngineIntent.HUMAN
            )

        policy = self.loader.get(current_business).state_policy_for(
            str(active.get(FlowField.PLUGIN_STATE))
        )
        if policy.switch_policy == SwitchPolicy.DENY:
            return self._chat_result(
                state,
                flows,
                reply="当前业务正在处理中，暂时不能切换。处理完成后我可以继续帮您办理其他业务。",
                intent=EngineIntent.SWITCH_DENIED,
                business=current_business,
            )
        if policy.switch_policy == SwitchPolicy.CONFIRM:
            # 先记住原话；用户确认后，新业务还要用这句话识别具体意图。
            pending = {
                PendingSwitchField.SOURCE_FLOW_ID: active[FlowField.FLOW_ID],
                PendingSwitchField.TARGET_TASK: target.route_task,
                PendingSwitchField.TARGET_BUSINESS: target.name,
                PendingSwitchField.TRIGGER_MESSAGE: state[ChatField.MESSAGE],
            }
            return self._chat_result(
                state,
                flows,
                reply=(
                    "当前业务还没有完成，是否暂停当前业务并切换到"
                    f"{ROUTE_TASK_LABELS.get(target.route_task, '新的业务流程')}？"
                ),
                intent=EngineIntent.CONFIRM_SWITCH,
                business=current_business,
                pending_switch=pending,
            )
        return self._switch_and_process(rerouted_state, flows, active, target.name)

    def _handle_pending_switch(
        self,
        state: ChatState,
        flows: FlowManager,
        pending: dict[str, Any],
    ) -> ChatState:
        """处理上一轮留下的“是否切换业务”问题。"""

        decision = self.llm.confirm_switch(state[ChatField.MESSAGE])
        active = flows.active()
        business = str(active[FlowField.BUSINESS]) if active else OTHER_BUSINESS
        if decision.decision == SwitchDecisionType.CANCEL:
            return self._chat_result(
                state,
                flows,
                reply="好的，已取消切换，继续处理当前业务。",
                intent=EngineIntent.CANCEL_SWITCH,
                business=business,
            )
        if decision.decision == SwitchDecisionType.UNKNOWN:
            return self._chat_result(
                state,
                flows,
                reply="请确认是否暂停当前业务并切换：回复“确认切换”或“取消切换”。",
                intent=EngineIntent.CONFIRM_SWITCH,
                business=business,
                pending_switch=pending,
            )
        if active is None or active.get(FlowField.FLOW_ID) != pending.get(
            PendingSwitchField.SOURCE_FLOW_ID
        ):
            # 确认期间原流程若已变动，就不能再按旧记录切换。
            return self._chat_result(
                state,
                flows,
                reply="原业务状态已经变化，本次切换已取消，请重新说明需要办理的业务。",
                intent=EngineIntent.CANCEL_SWITCH,
                business=business,
            )
        target_business = str(pending[PendingSwitchField.TARGET_BUSINESS])
        # 用户这句只是“确认切换”；新业务需要处理的是触发切换的原话。
        rerouted_state: ChatState = {
            **state,
            ChatField.MESSAGE: str(pending[PendingSwitchField.TRIGGER_MESSAGE]),
            ChatField.REROUTE_COUNT: MAX_INTERNAL_REROUTES,
            ChatField.PENDING_SWITCH: None,
        }
        return self._switch_and_process(rerouted_state, flows, active, target_business)

    def _switch_and_process(
        self,
        state: ChatState,
        flows: FlowManager,
        source: dict[str, Any],
        target_business: str,
    ) -> ChatState:
        """先保存原业务进度，再让目标业务处理同一条用户消息。"""

        flows.switch(source, target_business)
        return self._process_workflow(state, flows, target_business)

    @staticmethod
    def _chat_result(
        state: ChatState,
        flows: FlowManager,
        *,
        reply: str,
        intent: str,
        business: str,
        pending_switch: dict[str, Any] | None = None,
    ) -> ChatState:
        """生成 other 的答复，同时原样带回正在办理的业务进度。"""

        active = flows.active()
        return {
            **state,
            ChatField.BUSINESS: business,
            ChatField.PLUGIN_STATE: active.get(FlowField.PLUGIN_STATE) if active else None,
            ChatField.CONTEXT: deepcopy(active.get(FlowField.CONTEXT, {})) if active else {},
            ChatField.UNRECOGNIZED_COUNT: (
                int(active.get(FlowField.UNRECOGNIZED_COUNT, 0)) if active else 0
            ),
            ChatField.INTENT: intent,
            ChatField.SLOTS: {},
            ChatField.ACTION: ActionName.NONE,
            ChatField.ACTION_RESULT: action_success(),
            ChatField.REPLY: reply,
            ChatField.OUT: DialogueOutput.CHAT,
            ChatField.HANDOFF_REASON: None,
            ChatField.HANDLED_BY: OTHER_BUSINESS,
            ChatField.FLOWS: flows.flows,
            ChatField.ACTIVE_FLOW_ID: flows.active_flow_id,
            ChatField.PENDING_SWITCH: pending_switch,
        }


dialogue_engine = DialogueEngine()


def run_graph(state: ChatState) -> ChatState:
    """兼容现有 API：执行一轮对话。"""

    return dialogue_engine.run(state)
