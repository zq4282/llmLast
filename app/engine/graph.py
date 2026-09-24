"""唯一的一张业务图：新增业务只加插件，不复制流程图。"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.engine.action_result import action_failure
from app.engine.llm import intent_llm
from app.engine.loader import plugin_loader
from app.engine.outputs import DialogueOutput
from app.engine.recovery import (
    SYSTEM_FALLBACKS,
    recovery_decision,
)
from app.engine.render import render_template
from app.engine.route_tasks import ROUTE_TASK_UNKNOWN
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
        "handoff_reason": state.get("handoff_reason"),
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
    return get_graph().invoke(state)
