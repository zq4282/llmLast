"""LangGraph 的五个通用节点。业务差异全部来自插件配置和 handlers。"""

from typing import Any

from app.engine.llm import intent_llm
from app.engine.loader import plugin_loader
from app.engine.render import render_template
from app.engine.state import ChatState


def router(state: ChatState) -> dict[str, Any]:
    business = intent_llm.route(
        state["message"],
        plugin_loader.plugins,
        state.get("active_business"),
    )
    return {"business": business, "error": None}


def understand(state: ChatState) -> dict[str, Any]:
    plugin = plugin_loader.get(state["business"])
    context = state.get("context", {}) if state.get("active_business") == state["business"] else {}
    intent, slots = intent_llm.understand(
        state["message"],
        plugin,
        context,
    )
    return {"intent": intent, "slots": slots, "context": slots}


def decide(state: ChatState) -> dict[str, Any]:
    plugin = plugin_loader.get(state["business"])
    action_name = plugin.intents[state["intent"]].action
    action = plugin.actions[action_name]
    slots = state.get("slots", {})
    missing = [name for name in action.required_slots if not slots.get(name)]
    context = dict(slots)
    if missing:
        context["_pending_slots"] = missing
    else:
        context.pop("_pending_slots", None)
    return {"action": action_name, "missing_slots": missing, "context": context}


def execute(state: ChatState) -> dict[str, Any]:
    if state.get("missing_slots"):
        return {"action_result": {}}
    try:
        result = plugin_loader.execute(state["business"], state["action"], dict(state))
        return {"action_result": result}
    except Exception as exc:  # 外部适配器异常统一收敛，不能击穿聊天接口
        return {"action_result": {"ok": False, "error": "internal_error"}, "error": str(exc)}


def reply(state: ChatState) -> dict[str, Any]:
    plugin = plugin_loader.get(state["business"])
    missing = state.get("missing_slots", [])
    if missing:
        template_key = f"ask_{missing[0]}"
        template = plugin.templates.get(template_key, f"请补充 {missing[0]}。")
    else:
        result = state.get("action_result", {})
        if result.get("ok", True):
            action = plugin.actions[state["action"]]
            template = plugin.templates.get(action.success_template, plugin.templates.get("success", "已处理。"))
        else:
            error_code = result.get("error", "error")
            template = plugin.templates.get(
                f"error_{error_code}",
                plugin.templates.get("error", "处理失败，请稍后重试。"),
            )
    values = {**state.get("slots", {}), **state.get("action_result", {})}
    return {"reply": render_template(template, values)}


NODES = {
    "router": router,
    "understand": understand,
    "decide": decide,
    "execute": execute,
    "reply": reply,
}
