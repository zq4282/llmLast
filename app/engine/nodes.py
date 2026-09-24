"""兼容旧导入路径；节点实现已集中到 graph.py。"""

from app.engine.graph import decide, reply, router, run_action, understand

NODES = {
    "router": router,
    "understand": understand,
    "decide": decide,
    "run_action": run_action,
    "reply": reply,
}

__all__ = ["NODES", "router", "understand", "decide", "run_action", "reply"]
