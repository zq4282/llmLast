"""兼容旧导入路径；节点实现已集中到 graph.py。"""

from app.engine.graph import decide, execute, reply, router, understand

NODES = {
    "router": router,
    "understand": understand,
    "decide": decide,
    "execute": execute,
    "reply": reply,
}

__all__ = ["NODES", "router", "understand", "decide", "execute", "reply"]
