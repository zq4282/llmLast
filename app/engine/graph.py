"""唯一的一张业务图：新增业务只加插件，不复制流程图。"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.engine.action_result import action_failure
from app.engine.llm import intent_llm
from app.engine.loader import plugin_loader
from app.engine.render import render_template
from app.engine.state import ChatState


def router(state: ChatState) -> dict[str, Any]:
    """首次由模型选插件；锁定后只读共享状态，不再调用顶层 Router 模型。"""

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
            "error": None,
        }

    decision = intent_llm.classify_route(state["message"], state.get("history", []))
    plugin = plugin_loader.get_by_route_task(decision.task)
    return {
        "business": plugin.name,
        "route_task": decision.task,
        "route_confidence": decision.confidence,
        "route_locked": decision.task != "UNKNOWN",
        "error": None,
    }


def understand(state: ChatState) -> dict[str, Any]:
    """使用已选插件的 prompt 识别插件内意图并抽取槽位。"""

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

    plugin = plugin_loader.get(state["business"])
    plugin_state = state.get("plugin_state", plugin.initial_state)
    if plugin_state in plugin.terminal_states:
        return {
            "action": "none",
            "reply_key": "end",
            "next_plugin_state": plugin_state,
            "out": "END",
            "use_fallback": True,
        }

    transition = plugin.transition_for(plugin_state, state["intent"])
    if transition is None:
        fallback_key = state["intent"] if state["intent"] in plugin.fallbacks else "unknown"
        output = "HUMAN" if state["intent"] == "human" else "CHAT"
        return {
            "action": "none",
            "reply_key": fallback_key,
            "next_plugin_state": plugin_state,
            "out": output,
            "use_fallback": True,
        }
    return {
        "action": transition.action,
        "reply_key": transition.reply,
        "next_plugin_state": transition.next_state,
        "out": transition.out,
        "use_fallback": False,
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

    plugin = plugin_loader.get(state["business"])
    result = state.get("action_result", {})
    transition = plugin.transition_for(
        state.get("plugin_state", plugin.initial_state),
        state.get("intent", "unknown"),
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
            template = plugin.fallbacks.get(
                error_code,
                plugin.fallbacks.get("unknown", "处理失败，请重试。"),
            )
            next_plugin_state = state.get("plugin_state", plugin.initial_state)
            out = "CHAT"
    else:
        reply_key = state.get("reply_key", "unknown")
        if state.get("use_fallback"):
            template = plugin.fallbacks.get(reply_key, plugin.fallbacks.get("unknown", ""))
        else:
            template = plugin.templates.get(reply_key, plugin.fallbacks.get("unknown", ""))
        next_plugin_state = state.get("next_plugin_state", state.get("plugin_state", plugin.initial_state))
        out = state.get("out", "CHAT")
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
