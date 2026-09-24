"""把路由、理解、决策、执行、回复连成一张所有业务共用的图。"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.engine import nodes as _nodes
from app.engine.constants import ChatField, EngineIntent, MAX_INTERNAL_REROUTES, NodeName
from app.engine.llm import IntentLLM, intent_llm
from app.engine.loader import PluginLoader, plugin_loader
from app.engine.nodes import (
    ActionNode,
    DecisionNode,
    ReplyNode,
    RouterNode,
    UnderstandNode,
    decide,
    reply,
    run_action,
    understand,
)
from app.engine.state import ChatState


def router(state: ChatState) -> dict[str, Any]:
    """保留原来的调用方式，实际选业务的代码在 nodes 中。"""

    return _nodes.router(state, loader=plugin_loader)


class WorkflowGraph:
    """只规定五步的先后顺序，具体办什么由当前业务插件决定。"""

    def __init__(self, loader: PluginLoader, llm: IntentLLM) -> None:
        self.loader = loader
        self.llm = llm

    def _after_understand(self, state: ChatState) -> str:
        # 遇到其他话题时先停在这里，交给运行时判断是直接回答还是切换业务。
        plugin = self.loader.get(state[ChatField.BUSINESS])
        if (
            state.get(ChatField.INTENT) == EngineIntent.OTHER
            and plugin.other_enabled
            and int(state.get(ChatField.REROUTE_COUNT, 0)) < MAX_INTERNAL_REROUTES
        ):
            return EngineIntent.OTHER
        return NodeName.DECIDE

    def compile(self):
        """按固定顺序装好五个节点；具体业务内容在运行时从插件读取。"""

        self.loader.load_all()
        builder = StateGraph(ChatState)
        nodes = {
            NodeName.ROUTER: RouterNode(self.loader, self.llm),
            NodeName.UNDERSTAND: UnderstandNode(self.loader, self.llm),
            NodeName.DECIDE: DecisionNode(self.loader),
            NodeName.RUN_ACTION: ActionNode(self.loader),
            NodeName.REPLY: ReplyNode(self.loader),
        }
        for name, node in nodes.items():
            builder.add_node(name, node)

        builder.add_edge(START, NodeName.ROUTER)
        builder.add_edge(NodeName.ROUTER, NodeName.UNDERSTAND)
        builder.add_conditional_edges(
            NodeName.UNDERSTAND,
            self._after_understand,
            {EngineIntent.OTHER: END, NodeName.DECIDE: NodeName.DECIDE},
        )
        builder.add_edge(NodeName.DECIDE, NodeName.RUN_ACTION)
        builder.add_edge(NodeName.RUN_ACTION, NodeName.REPLY)
        builder.add_edge(NodeName.REPLY, END)
        return builder.compile()


def get_graph():
    """每次调用都重新构建并编译流程图。"""

    return WorkflowGraph(plugin_loader, intent_llm).compile()


def run_graph(state: ChatState) -> ChatState:
    """保留原来的导入路径；实际处理一轮对话的代码放在 runtime 中。"""

    from app.engine.runtime import run_graph as run_turn

    return run_turn(state)
