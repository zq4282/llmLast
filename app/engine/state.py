"""LangGraph 在五个节点之间传递的状态。"""

from enum import StrEnum
from typing import Any, TypedDict

from app.engine.action_result import ActionResult
from app.engine.outputs import DialogueOutput


class CallInfoState(TypedDict):
    """一次通话的共享元数据，字段名统一使用后端 snake_case。"""

    caller: str
    callee: str
    call_start_time: str


class FlowState(TypedDict, total=False):
    """一个可暂停、恢复或完成的业务流程快照。"""

    flow_id: str
    business: str
    plugin_state: str
    status: str
    context: dict[str, Any]
    unrecognized_count: int
    completed_actions: list[dict[str, Any]]


class ReplySource(StrEnum):
    TEMPLATE = "template"
    PLUGIN_FALLBACK = "plugin_fallback"
    SYSTEM_FALLBACK = "system_fallback"


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
    # 【单轮必需】租户标识；业务 Handler 可直接从 state 读取。
    tenant_id: int
    # 【跨轮必需】通话元数据；所有图节点及业务 Handler 共享，随会话持久化。
    call_info: CallInfoState

    # ===== 顶层路由（跨轮共享） =====
    # 【必需】已选中的插件名，例如 refund；值不对应插件时下一轮重新路由。
    business: str
    # 【必需】插件内状态机位置，例如 IDLE/CONFIRM_REFUND/ASK_OTHER。
    plugin_state: str
    # 【跨轮必需】多业务流程快照；business/plugin_state 是 ACTIVE 流程兼容投影。
    flows: list[FlowState]
    active_flow_id: str | None
    # 【跨轮可选】需要用户确认后才能执行的插件切换。
    pending_switch: dict[str, Any] | None

    # ===== 会话恢复控制（跨轮共享） =====
    # 连续未理解或当前插件无法处理的次数；匹配有效业务动作后立即清零。
    unrecognized_count: int

    # ===== 插件理解结果（单轮临时） =====
    # 【必需】understand 节点返回的插件内意图，decide 用它查动作表。
    intent: str
    # 【本轮需要】本轮模型新抽取的槽位；action handler 会按需与 context 合并。
    slots: dict[str, Any]
    # 【跨轮必需】已累积的槽位和 API 结果，例如 order_no/amount/merchant。
    context: dict[str, Any]

    # ===== 动作表决策（单轮临时） =====
    # 【必需】actions.do，例如 query_order/submit_refund/none。
    action: str | None
    # 【必需】actions.reply 或 fallback key，reply 节点用它选话术。
    reply_key: str
    # 【必需】actions.next；成功后写回 plugin_state，失败时可由 on_error 覆盖。
    next_plugin_state: str
    # 【必需】actions.out，对外标识 CHAT/REFUND/UNSUBSCRIBE/REFUND_UNSUBSCRIBE/HUMAN/END。
    out: DialogueOutput
    # 【单轮临时】转人工原因，仅随 HUMAN 响应返回，不持久化。
    handoff_reason: str | None
    # 【单轮临时】回复来自正常模板、插件兜底或系统兜底。
    reply_source: ReplySource
    # 【单轮临时】Router 已直接产出 unknown/human 时跳过插件理解。
    skip_understanding: bool
    # 【单轮临时】other 二级路由次数，最大为 1。
    reroute_count: int
    # 【单轮临时】实际回答本轮消息的插件。
    handled_by: str

    # ===== 执行与回复（单轮临时/最终输出） =====
    # 【本轮需要】run_action 调用业务 API 后的结果，reply 用于填充话术和更新 context。
    action_result: ActionResult
    # 【最终输出】对用户的回复文本。
    reply: str
