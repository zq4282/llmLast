"""共享业务图，以及无状态 other 覆盖层的流程编排。"""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.engine.action_result import action_failure, action_success
from app.engine.llm import intent_llm
from app.engine.loader import PluginConfigError, SwitchPolicy, plugin_loader
from app.engine.outputs import DialogueOutput
from app.engine.recovery import (
    SYSTEM_FALLBACKS,
    recovery_decision,
)
from app.engine.render import render_template
from app.engine.route_tasks import (
    ROUTE_TASK_DEFINITIONS,
    ROUTE_TASK_LABELS,
    ROUTE_TASK_UNKNOWN,
)
from app.engine.state import ChatState, ReplySource


def _use_plugin_recovery_reply(
    state: ChatState,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """优先使用插件按当前状态配置的恢复话术，计数和升级仍由引擎负责。"""

    business = state.get("business")
    if not business or business not in plugin_loader.plugins:
        return decision
    plugin = plugin_loader.get(business)
    plugin_state = state.get("plugin_state")
    if plugin_state not in plugin.states:
        plugin_state = plugin.initial_state
    contextual_key = f"{plugin_state.lower()}_{decision['reply_key']}"
    if contextual_key in plugin.fallbacks:
        reply_key = contextual_key
    elif decision["reply_key"] in plugin.fallbacks:
        reply_key = decision["reply_key"]
    else:
        return decision
    return {
        **decision,
        "reply_key": reply_key,
        "reply_source": ReplySource.PLUGIN_FALLBACK,
    }


def _recovery_for_state(
    state: ChatState,
    *,
    trigger: str | None = None,
) -> dict[str, Any] | None:
    """读取插件恢复步骤；没有已选插件时使用引擎默认步骤。"""

    resolved_trigger = trigger or ("unknown" if state.get("intent") == "unknown" else None)
    steps = None
    business = state.get("business")
    if resolved_trigger and business and business in plugin_loader.plugins:
        steps = plugin_loader.get(business).recovery_steps_for(resolved_trigger)
    decision = recovery_decision(dict(state), trigger=trigger, steps=steps)
    return _use_plugin_recovery_reply(state, decision) if decision is not None else None


def router(state: ChatState) -> dict[str, Any]:
    """首次由模型选插件；锁定后只读共享状态，不再调用顶层 Router 模型。"""

    existing_business = state.get("business")
    if existing_business and existing_business in plugin_loader.plugins:
        plugin = plugin_loader.get(existing_business)
        return {
            "business": plugin.name,
            "skip_understanding": False,
        }

    decision = intent_llm.classify_route(state["message"], state.get("history", []))
    if decision.task == ROUTE_TASK_UNKNOWN:
        return {
            "business": state.get("business") or "system",
            "intent": "unknown",
            "slots": {},
            "skip_understanding": True,
        }
    plugin = plugin_loader.get_by_route_task(decision.task)
    return {
        "business": plugin.name,
        "skip_understanding": False,
    }


def understand(state: ChatState) -> dict[str, Any]:
    """使用已选插件的 prompt 识别插件内意图并抽取槽位。"""

    if state.get("skip_understanding"):
        return {}

    plugin = plugin_loader.get(state["business"])
    plugin_state = state.get("plugin_state")
    if plugin_state not in plugin.states:
        plugin_state = plugin.initial_state
    context = dict(state.get("context", {}))
    decision = intent_llm.understand(
        state["message"],
        plugin,
        plugin_state,
        context,
        state.get("history", []),
    )
    return {
        "plugin_state": plugin_state,
        "intent": decision.intent,
        "slots": decision.slots,
    }


def decide(state: ChatState) -> dict[str, Any]:
    """根据 plugin_state + intent 查询插件动作表。"""

    recovery = _recovery_for_state(state)
    if recovery is not None:
        return recovery

    plugin = plugin_loader.get(state["business"])
    plugin_state = state.get("plugin_state", plugin.initial_state)
    if plugin_state in plugin.terminal_states:
        return {
            "action": "none",
            "reply_key": "end",
            "next_plugin_state": plugin_state,
            "out": DialogueOutput.END,
            "reply_source": ReplySource.PLUGIN_FALLBACK,
            "unrecognized_count": 0,
            "handoff_reason": None,
        }

    transition = plugin.transition_for(plugin_state, state["intent"])
    if transition is None:
        # 模型可能把乱码误判成 other，也可能返回插件尚未配置的新意图。
        # 这两种情况都不能清零并无限重复同一句兜底；保持业务状态并进入分级恢复。
        unsupported = _recovery_for_state(state, trigger="unsupported")
        assert unsupported is not None
        return unsupported
    return {
        "action": transition.action,
        "reply_key": transition.reply,
        "next_plugin_state": transition.next_state,
        "out": transition.out,
        "reply_source": ReplySource.TEMPLATE,
        "unrecognized_count": 0,
        "handoff_reason": (
            "USER_REQUESTED" if transition.out == DialogueOutput.HUMAN else None
        ),
    }


def run_action(state: ChatState) -> dict[str, Any]:
    """图节点：运行 decide 选中的一个业务 action。"""

    try:
        result = plugin_loader.invoke_action_handler(
            state["business"],
            state.get("action", "none"),
            dict(state),
        )
        return {"action_result": result}
    except Exception:  # 外部适配器异常统一收敛，不能击穿聊天接口
        return {"action_result": action_failure("internal_error")}


def reply(state: ChatState) -> dict[str, Any]:
    """渲染插件话术，合并业务结果，并推进插件状态。"""

    result = state.get("action_result", {})
    reply_source = state.get("reply_source", ReplySource.TEMPLATE)
    plugin = (
        None
        if reply_source == ReplySource.SYSTEM_FALLBACK
        else plugin_loader.get(state["business"])
    )
    transition = (
        plugin.transition_for(
            state.get("plugin_state", plugin.initial_state),
            state.get("intent", "unknown"),
        )
        if plugin
        else None
    )
    if not result.get("ok", True):
        error_code = str(result.get("error", "unknown"))
        error_transition = transition.error_transition_for(error_code) if transition else None
        if error_transition:
            template = plugin.templates.get(
                error_transition.reply,
                plugin.fallbacks.get(error_transition.reply, plugin.fallbacks.get("unknown", "")),
            )
            next_plugin_state = error_transition.next_state
            out = error_transition.out
        else:
            # 当前 action 未声明该错误码时，才进入插件级全局兜底。
            assert plugin is not None
            template = plugin.fallbacks.get(
                error_code,
                plugin.fallbacks.get("unknown", "处理失败，请重试。"),
            )
            next_plugin_state = state.get("plugin_state", plugin.initial_state)
            out = DialogueOutput.CHAT
    else:
        reply_key = state.get("reply_key", "unknown")
        if reply_source == ReplySource.SYSTEM_FALLBACK:
            template = SYSTEM_FALLBACKS.get(reply_key, SYSTEM_FALLBACKS["unknown_first"])
        elif reply_source == ReplySource.PLUGIN_FALLBACK:
            assert plugin is not None
            template = plugin.fallbacks.get(reply_key, plugin.fallbacks.get("unknown", ""))
        else:
            assert plugin is not None
            template = plugin.templates.get(reply_key, plugin.fallbacks.get("unknown", ""))
        default_plugin_state = plugin.initial_state if plugin else state.get("plugin_state")
        next_plugin_state = state.get(
            "next_plugin_state",
            state.get("plugin_state", default_plugin_state),
        )
        out: DialogueOutput = state.get("out", DialogueOutput.CHAT)
    values = {**state.get("context", {}), **state.get("slots", {}), **result}
    if result.get("ok", True):
        context = {
            key: value
            for key, value in values.items()
            if key not in {"ok", "error"}
        }
    else:
        # 业务未完成或技术失败时，不持久化本轮无效参数。
        context = dict(state.get("context", {}))
    return {
        "reply": render_template(template, values),
        "plugin_state": next_plugin_state,
        "context": context,
        "out": out,
        "unrecognized_count": state.get("unrecognized_count", 0),
        "handoff_reason": (
            state.get("handoff_reason") if out == DialogueOutput.HUMAN else None
        ),
    }


def get_graph():
    plugin_loader.load_all()
    builder = StateGraph(ChatState)

    # 路由分发
    builder.add_node("router", router)
    # 理解
    builder.add_node("understand", understand)
    # 决定
    builder.add_node("decide", decide)
    # 执行 decide 选中的业务 action
    builder.add_node("run_action", run_action)
    # 回复
    builder.add_node("reply", reply)

    builder.add_edge(START, "router")
    builder.add_edge("router", "understand")
    builder.add_edge("understand", "decide")
    builder.add_edge("decide", "run_action")
    builder.add_edge("run_action", "reply")
    builder.add_edge("reply", END)
    return builder.compile()


def run_graph(state: ChatState) -> ChatState:
    """执行一轮对话。

    workflow 插件仍复用 understand/decide/run_action/reply 五个节点；本函数只负责编排
    ACTIVE/SUSPENDED 流程、无状态 other 覆盖层和单次内部重路由。
    """

    plugin_loader.load_all()
    working: dict[str, Any] = dict(state)
    flows, active_flow_id = _normalize_flows(working)
    pending_switch = deepcopy(working.get("pending_switch"))

    if pending_switch:
        return _handle_pending_switch(
            working,
            flows,
            active_flow_id,
            pending_switch,
        )

    active = _active_flow(flows, active_flow_id)
    if active is not None:
        plugin = plugin_loader.get(str(active["business"]))
        if str(active.get("plugin_state")) in plugin.terminal_states:
            active["status"] = "COMPLETED"
            active_flow_id = None
        else:
            return _process_workflow(working, flows, active_flow_id, plugin.name)

    route = intent_llm.classify_route(working["message"], working.get("history", []))
    try:
        plugin = plugin_loader.get_by_route_task(route.task)
    except PluginConfigError:
        # 已声明但尚未安装固定插件的 Task 也交给 other 动态回答，避免接口报错。
        return _process_other(working, flows, None)
    if plugin.is_overlay or route.task == ROUTE_TASK_UNKNOWN:
        return _process_other(working, flows, None)

    active_flow_id = _activate_target_flow(flows, plugin.name)
    return _process_workflow(working, flows, active_flow_id, plugin.name)


def _normalize_flows(state: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    raw_flows = state.get("flows")
    flows = deepcopy(raw_flows) if isinstance(raw_flows, list) else []
    active_flow_id = state.get("active_flow_id")
    active = _active_flow(flows, active_flow_id)
    if active is not None:
        return flows, str(active["flow_id"])

    # 兼容升级前仅保存 business/plugin_state/context 的会话，以及直接调用 run_graph 的代码。
    business = state.get("business")
    if business and business in plugin_loader.plugins:
        plugin = plugin_loader.get(str(business))
        if not plugin.is_overlay:
            flow = _new_flow(
                plugin.name,
                plugin_state=state.get("plugin_state") or plugin.initial_state,
                context=state.get("context", {}),
                unrecognized_count=state.get("unrecognized_count", 0),
            )
            flows.append(flow)
            return flows, str(flow["flow_id"])
    return flows, None


def _new_flow(
    business: str,
    *,
    plugin_state: str | None = None,
    context: dict[str, Any] | None = None,
    unrecognized_count: int = 0,
    completed_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plugin = plugin_loader.get(business)
    return {
        "flow_id": f"flow_{uuid4().hex}",
        "business": business,
        "plugin_state": plugin_state or plugin.initial_state,
        "status": "ACTIVE",
        "context": deepcopy(context or {}),
        "unrecognized_count": max(0, int(unrecognized_count)),
        "completed_actions": deepcopy(completed_actions or []),
    }


def _active_flow(
    flows: list[dict[str, Any]],
    active_flow_id: str | None,
) -> dict[str, Any] | None:
    if active_flow_id:
        matched = next(
            (
                flow
                for flow in flows
                if flow.get("flow_id") == active_flow_id and flow.get("status") == "ACTIVE"
            ),
            None,
        )
        if matched is not None:
            return matched
    return next((flow for flow in reversed(flows) if flow.get("status") == "ACTIVE"), None)


def _activate_target_flow(
    flows: list[dict[str, Any]],
    business: str,
    *,
    context: dict[str, Any] | None = None,
    completed_actions: list[dict[str, Any]] | None = None,
) -> str:
    suspended = next(
        (
            flow
            for flow in reversed(flows)
            if flow.get("business") == business and flow.get("status") == "SUSPENDED"
        ),
        None,
    )
    if suspended is not None:
        suspended["status"] = "ACTIVE"
        if context:
            suspended["context"] = {**context, **suspended.get("context", {})}
        if completed_actions:
            suspended["completed_actions"] = _merge_completed_actions(
                completed_actions,
                suspended.get("completed_actions", []),
            )
        return str(suspended["flow_id"])
    flow = _new_flow(
        business,
        context=context,
        completed_actions=completed_actions,
    )
    flows.append(flow)
    return str(flow["flow_id"])


def _process_workflow(
    state: dict[str, Any],
    flows: list[dict[str, Any]],
    active_flow_id: str,
    business: str,
    *,
    forced_intent: str | None = None,
) -> ChatState:
    flow = _active_flow(flows, active_flow_id)
    if flow is None:
        raise RuntimeError("ACTIVE 流程不存在")
    plugin = plugin_loader.get(business)
    working = {
        **state,
        "business": business,
        "plugin_state": flow.get("plugin_state") or plugin.initial_state,
        "context": deepcopy(flow.get("context", {})),
        "unrecognized_count": int(flow.get("unrecognized_count", 0)),
        "completed_actions": deepcopy(flow.get("completed_actions", [])),
        "skip_understanding": False,
    }
    if forced_intent:
        working.update({"intent": forced_intent, "slots": {}})
    else:
        working.update(understand(working))

    if (
        working.get("intent") == "other"
        and plugin.other_enabled
        and int(working.get("reroute_count", 0)) < 1
    ):
        return _process_other(working, flows, active_flow_id)

    working.update(decide(working))
    working.update(run_action(working))
    working.update(reply(working))
    return _commit_workflow_result(working, flows, active_flow_id)


def _commit_workflow_result(
    result: dict[str, Any],
    flows: list[dict[str, Any]],
    active_flow_id: str,
) -> ChatState:
    flow = _active_flow(flows, active_flow_id)
    if flow is None:
        raise RuntimeError("提交结果时 ACTIVE 流程不存在")
    flow["plugin_state"] = result.get("plugin_state")
    flow["context"] = deepcopy(result.get("context", {}))
    flow["unrecognized_count"] = int(result.get("unrecognized_count", 0))

    output = result.get("out", DialogueOutput.CHAT)
    action_result = result.get("action_result", {})
    if action_result.get("ok") and output in {
        DialogueOutput.REFUND,
        DialogueOutput.UNSUBSCRIBE,
        DialogueOutput.REFUND_UNSUBSCRIBE,
    }:
        completed = {
            "action": str(result.get("action") or output.value.lower()),
            "out": output.value,
            "order_no": action_result.get("order_no"),
            "status": "SUCCESS",
        }
        flow["completed_actions"] = _merge_completed_actions(
            flow.get("completed_actions", []),
            [completed],
        )

    next_active_id: str | None = active_flow_id
    if output in {DialogueOutput.HUMAN, DialogueOutput.END}:
        flow["status"] = "COMPLETED"
        next_active_id = None

    return {
        **result,
        "handled_by": str(result["business"]),
        "flows": flows,
        "active_flow_id": next_active_id,
        "pending_switch": None,
    }


def _process_other(
    state: dict[str, Any],
    flows: list[dict[str, Any]],
    active_flow_id: str | None,
) -> ChatState:
    other = plugin_loader.get_by_route_task(ROUTE_TASK_UNKNOWN)
    available_tasks = {
        plugin.route_task: ROUTE_TASK_DEFINITIONS[plugin.route_task]
        for plugin in plugin_loader.plugins.values()
        if not plugin.is_overlay and plugin.route_task in ROUTE_TASK_DEFINITIONS
    }
    config = state.get("config", {})
    decision = intent_llm.handle_other(
        state["message"],
        other,
        available_tasks,
        system_prompt=str(state.get("system_prompt") or "你是会员业务客服"),
        max_reply_len=int(config.get("max_reply_len", 60)),
    )
    active = _active_flow(flows, active_flow_id)
    if decision.decision == "ANSWER":
        reply_text = decision.reply or ""
        if active is not None:
            plugin = plugin_loader.get(str(active["business"]))
            policy = plugin.state_policy_for(str(active.get("plugin_state")))
            if policy.remind_after_other:
                reminder = render_template(policy.reminder, active.get("context", {}))
                reply_text = f"{reply_text}{reminder}"
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply=reply_text,
            intent=decision.intent or "chitchat",
            business=str(active["business"]) if active else other.name,
        )

    if int(state.get("reroute_count", 0)) >= 1:
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply="当前消息暂时无法继续转交，请直接说明需要办理的业务。",
            intent="unknown",
            business=str(active["business"]) if active else other.name,
        )

    target = plugin_loader.get_by_route_task(decision.target_task or "")
    rerouted_state = {**state, "reroute_count": 1}
    if active is None:
        target_id = _activate_target_flow(flows, target.name)
        return _process_workflow(rerouted_state, flows, target_id, target.name)

    current_business = str(active["business"])
    if target.name == current_business:
        return _process_workflow(
            rerouted_state,
            flows,
            str(active["flow_id"]),
            current_business,
        )

    # 活动业务中的转人工始终由当前业务插件处理，不切到独立 human 插件。
    if target.route_task == DialogueOutput.HUMAN.value:
        return _process_workflow(
            rerouted_state,
            flows,
            str(active["flow_id"]),
            current_business,
            forced_intent="human",
        )

    current_plugin = plugin_loader.get(current_business)
    policy = current_plugin.state_policy_for(str(active.get("plugin_state")))
    if policy.switch_policy == SwitchPolicy.DENY:
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply="当前业务正在处理中，暂时不能切换。处理完成后我可以继续帮您办理其他业务。",
            intent="switch_denied",
            business=current_business,
        )
    if policy.switch_policy == SwitchPolicy.CONFIRM:
        pending = {
            "source_flow_id": active["flow_id"],
            "target_task": target.route_task,
            "target_business": target.name,
            "trigger_message": state["message"],
        }
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply=(
                "当前业务还没有完成，是否暂停当前业务并切换到"
                f"{ROUTE_TASK_LABELS.get(target.route_task, '新的业务流程')}？"
            ),
            intent="confirm_switch",
            business=current_business,
            pending_switch=pending,
        )
    return _switch_and_process(rerouted_state, flows, active, target.name)


def _handle_pending_switch(
    state: dict[str, Any],
    flows: list[dict[str, Any]],
    active_flow_id: str | None,
    pending: dict[str, Any],
) -> ChatState:
    decision = intent_llm.confirm_switch(state["message"])
    active = _active_flow(flows, active_flow_id)
    business = str(active["business"]) if active else "other"
    if decision.decision == "CANCEL":
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply="好的，已取消切换，继续处理当前业务。",
            intent="cancel_switch",
            business=business,
        )
    if decision.decision == "UNKNOWN":
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply="请确认是否暂停当前业务并切换：回复“确认切换”或“取消切换”。",
            intent="confirm_switch",
            business=business,
            pending_switch=pending,
        )
    if active is None or active.get("flow_id") != pending.get("source_flow_id"):
        return _chat_result(
            state,
            flows,
            active_flow_id,
            reply="原业务状态已经变化，本次切换已取消，请重新说明需要办理的业务。",
            intent="cancel_switch",
            business=business,
        )
    target_business = str(pending["target_business"])
    rerouted_state = {
        **state,
        "message": str(pending["trigger_message"]),
        "reroute_count": 1,
        "pending_switch": None,
    }
    return _switch_and_process(rerouted_state, flows, active, target_business)


def _switch_and_process(
    state: dict[str, Any],
    flows: list[dict[str, Any]],
    source: dict[str, Any],
    target_business: str,
) -> ChatState:
    merge = target_business == "refund_unsubscribe" and source.get("business") in {
        "refund",
        "unsubscribe",
    }
    source["status"] = "SUPERSEDED" if merge else "SUSPENDED"
    target_id = _activate_target_flow(
        flows,
        target_business,
        context=source.get("context", {}) if merge else None,
        completed_actions=source.get("completed_actions", []) if merge else None,
    )
    return _process_workflow(state, flows, target_id, target_business)


def _chat_result(
    state: dict[str, Any],
    flows: list[dict[str, Any]],
    active_flow_id: str | None,
    *,
    reply: str,
    intent: str,
    business: str,
    pending_switch: dict[str, Any] | None = None,
) -> ChatState:
    active = _active_flow(flows, active_flow_id)
    return {
        **state,
        "business": business,
        "plugin_state": active.get("plugin_state") if active else None,
        "context": deepcopy(active.get("context", {})) if active else {},
        "unrecognized_count": int(active.get("unrecognized_count", 0)) if active else 0,
        "intent": intent,
        "slots": {},
        "action": "none",
        "action_result": action_success(),
        "reply": reply,
        "out": DialogueOutput.CHAT,
        "handoff_reason": None,
        "handled_by": "other",
        "flows": flows,
        "active_flow_id": active_flow_id,
        "pending_switch": pending_switch,
    }


def _merge_completed_actions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for group in groups:
        for item in group:
            key = (item.get("action"), item.get("out"), item.get("order_no"))
            if key not in seen:
                seen.add(key)
                merged.append(deepcopy(item))
    return merged
