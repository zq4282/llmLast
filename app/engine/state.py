"""LangGraph 在五个节点之间传递的状态。"""

from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    """统一图状态。

    【跨轮共享】字段需由 SessionStore/Redis 保存，下一轮再传入图。
    【单轮临时】字段只在本次 router -> reply 执行中传递。
    """

    # ===== 请求输入 =====
    # 【追踪可选】会话标识；图内逻辑不依赖，API/日志/会话存储使用。
    session_id: str
    # 【单轮必需】当前用户原始输入。
    message: str
    # 【跨轮必需】最近对话，Router 和插件 prompt 都可能用到。
    history: list[dict[str, str]]

    # ===== 顶层路由（跨轮共享） =====
    # 【必需】Router 模型的 Task 结果，例如 REFUND；用于复用路由。
    route_task: str | None
    # 【可选】Router 置信度，仅用于观测/审计，删除不影响流程。
    route_confidence: float | None
    # 【必需】True 表示已选定插件，后续轮次禁止再调用顶层 Router 模型。
    route_locked: bool
    # 【必需】已选中的插件名，例如 refund；后续轮次直接据此进入插件。
    business: str
    # 【必需】插件内状态机位置，例如 IDLE/CONFIRM_REFUND/ASK_OTHER。
    plugin_state: str

    # ===== 插件理解结果（单轮临时） =====
    # 【必需】understand 节点返回的插件内意图，decide 用它查动作表。
    intent: str
    # 【可选】插件内意图置信度，仅用于观测，删除不影响流程。
    intent_confidence: float
    # 【本轮需要】本轮模型抽取并与上下文合并后的槽位，execute 使用。
    slots: dict[str, Any]
    # 【跨轮必需】已累积的槽位和 API 结果，例如 order_id/amount/merchant。
    context: dict[str, Any]

    # ===== 动作表决策（单轮临时） =====
    # 【必需】actions.do，例如 query_order/submit_refund/none。
    action: str | None
    # 【必需】actions.reply 或 fallback key，reply 节点用它选话术。
    reply_key: str
    # 【必需】actions.next；reply 成功后写回 plugin_state。
    next_plugin_state: str
    # 【必需】actions.out，对外标识 CHAT/REFUND/HUMAN。
    out: str
    # 【内部需要】区分 templates 和 fallbacks 话术来源。可通过拆分 reply_key 类型后移除。
    use_fallback: bool

    # ===== 执行与回复（单轮临时/最终输出） =====
    # 【本轮需要】execute 调用业务 API 后的结果，reply 用于填充话术和更新 context。
    action_result: dict[str, Any]
    # 【最终输出】对用户的回复文本。
    reply: str
    # 【可选】内部错误详情，用于日志/排查；不应直接返回给用户。
    error: str | None
