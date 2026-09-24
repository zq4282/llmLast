"""唯一的一张业务图：新增业务只加插件，不复制流程图。"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.engine.action_result import action_failure
from app.engine.llm import intent_llm
from app.engine.loader import plugin_loader
from app.engine.outputs import DialogueOutput
from app.engine.recovery import (
    ACTIVE_HANDOFF_STATUSES,
    SYSTEM_FALLBACKS,
    recovery_decision,
)
from app.engine.render import render_template
from app.engine.state import ChatState


def router(state: ChatState) -> dict[str, Any]:
    """首次由模型选插件；锁定后只读共享状态，不再调用顶层 Router 模型。"""

    if state.get("conversation_status", "BOT") in ACTIVE_HANDOFF_STATUSES:
        return {
            "business": state.get("business") or "system",
            "intent": "human",
            "intent_confidence": 1.0,
            "slots": {},
            "skip_understanding": True,
            "error": None,
        }

    route_task = state.get("route_task")
    if state.get("route_locked") and route_task and route_task != "UNKNOWN":
        existing_business = state.get("business")
        plugin = (
            plugin_loader.get(existing_business)
            if existing_business and existing_business in plugin_loader.plugins
            else plugin_loader.get_by_route_task(route_task)
        )
        return {
            "business": plugin.name,
            "route_task": route_task,
            "route_confidence": state.get("route_confidence"),
            "route_locked": True,
            "skip_understanding": False,
            "error": None,
        }

    decision = intent_llm.classify_route(state["message"], state.get("history", []))
    if decision.task in {"UNKNOWN", DialogueOutput.HUMAN.value}:
        return {
            "business": state.get("business") or "system",
            "route_task": decision.task,
            "route_confidence": decision.confidence,
            "route_locked": decision.task == DialogueOutput.HUMAN.value,
            "intent": "human" if decision.task == DialogueOutput.HUMAN.value else "unknown",
            "intent_confidence": decision.confidence,
            "slots": {},
            "skip_understanding": True,
            "error": None,
        }
    plugin = plugin_loader.get_by_route_task(decision.task)
    return {
        "business": plugin.name,
        "route_task": decision.task,
        "route_confidence": decision.confidence,
        "route_locked": decision.task != "UNKNOWN",
        "skip_understanding": False,
        "error": None,
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
        "intent_confidence": decision.confidence,
        "slots": decision.slots,
    }


def decide(state: ChatState) -> dict[str, Any]:
    """根据 plugin_state + intent 查询插件动作表。"""

    recovery = recovery_decision(dict(state))
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
            "use_fallback": True,
            "use_system_fallback": False,
            "unrecognized_count": 0,
            "conversation_status": "ENDED",
            "handoff_reason": None,
        }

    transition = plugin.transition_for(plugin_state, state["intent"])
    if transition is None:
        return {
            "action": "none",
            "reply_key": "unsupported_intent",
            "next_plugin_state": plugin_state,
            "out": DialogueOutput.CHAT,
            "use_fallback": True,
            "use_system_fallback": True,
            "unrecognized_count": 0,
            "conversation_status": "BOT",
            "handoff_reason": None,
        }
    next_status = "ENDED" if transition.out == DialogueOutput.END else "BOT"
    return {
        "action": transition.action,
        "reply_key": transition.reply,
        "next_plugin_state": transition.next_state,
        "out": transition.out,
        "use_fallback": False,
        "use_system_fallback": False,
        "unrecognized_count": 0,
        "conversation_status": next_status,
        "handoff_reason": None,
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
    except Exception as exc:  # 外部适配器异常统一收敛，不能击穿聊天接口
        return {"action_result": action_failure("internal_error"), "error": str(exc)}


def reply(state: ChatState) -> dict[str, Any]:
    """渲染插件话术，合并业务结果，并推进插件状态。"""

    result = state.get("action_result", {})
    plugin = None if state.get("use_system_fallback") else plugin_loader.get(state["business"])
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
        if state.get("use_system_fallback"):
            template = SYSTEM_FALLBACKS.get(reply_key, SYSTEM_FALLBACKS["unknown_first"])
        elif state.get("use_fallback"):
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
        "conversation_status": state.get("conversation_status", "BOT"),
        "handoff_reason": state.get("handoff_reason"),
        "handoff_id": state.get("handoff_id"),
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
