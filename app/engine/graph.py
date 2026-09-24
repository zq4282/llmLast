"""唯一的一张业务图：新增业务只加插件，不复制流程图。"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.engine.loader import plugin_loader
from app.engine.nodes import NODES
from app.engine.state import ChatState


@lru_cache(maxsize=1)
def get_graph():
    plugin_loader.load_all()
    builder = StateGraph(ChatState)
    for name, node in NODES.items():
        builder.add_node(name, node)

    builder.add_edge(START, "router")
    builder.add_edge("router", "understand")
    builder.add_edge("understand", "decide")
    builder.add_edge("decide", "execute")
    builder.add_edge("execute", "reply")
    builder.add_edge("reply", END)
    return builder.compile()


def run_graph(state: ChatState) -> ChatState:
    return get_graph().invoke(state)
