"""说明一轮对话会传递哪些数据，以及哪些数据要留到下一轮。"""

from enum import StrEnum
from typing import Any, TypedDict

from app.engine.action_result import ActionResult
from app.engine.outputs import DialogueOutput


class CallInfoState(TypedDict):
    """一次通话的共享元数据，字段名统一使用后端 snake_case。"""

    caller: str  # 主叫号码。
    callee: str  # 被叫号码。
    call_start_time: str  # 通话开始时间，沿用请求中的字符串格式。


class FlowState(TypedDict, total=False):
    """一个可暂停、恢复或完成的业务流程快照。"""

    # total=False 允许只传部分字段，不代表字段值可以为 None。
    flow_id: str  # 流程实例标识，同一业务可有不同实例。
    business: str  # 负责此流程的插件名。
    plugin_state: str  # 暂停后恢复时继续执行的状态机位置。
    # 流程生命周期，取值见 FlowStatus；与插件内部的 plugin_state 分开管理。
    status: str
    context: dict[str, Any]  # 此流程累积的槽位和业务查询结果。
    unrecognized_count: int  # 此流程连续未理解的次数，随流程一起恢复。
    # 已成功办理的动作记录，包含 action/out/order_no/status，供组合业务复用。
    completed_actions: list[dict[str, Any]]


class ReplySource(StrEnum):
    """回复话术的来源，供 reply 节点选择模板及兜底策略。"""

    TEMPLATE = "template"  # 动作表指定的正常回复模板。
    PLUGIN_FALLBACK = "plugin_fallback"  # 当前业务插件提供的兜底话术。
    SYSTEM_FALLBACK = "system_fallback"  # 引擎统一提供的系统兜底话术。


class ChatState(TypedDict, total=False):
    """统一图状态。

    【跨轮共享】字段需由 SessionStore/Redis 保存，下一轮再传入图。
    【单轮临时】字段只在本次 router -> reply 执行中传递。
    【流程工作副本】从活动 FlowState 取出，处理后写回 flows，不单独持久化。
    total=False 允许各节点只返回本次更新的字段，由图合并到已有状态。
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
    # 【单轮可选】回复约束和 other 问答所需的系统提示词。
    system_prompt: str
    # 【单轮可选】运行配置，例如 max_reply_len 用于约束 other 回复长度。
    config: dict[str, Any]

    # ===== 当前业务与流程管理 =====
    # 【流程工作副本】本轮执行的插件名，例如 refund，同时用于 API 返回。
    business: str
    # 【流程工作副本】插件内状态机位置，例如 IDLE/CONFIRM_REFUND/ASK_OTHER。
    plugin_state: str
    # 【跨轮必需】多业务流程快照，是业务进度的唯一持久化来源。
    flows: list[FlowState]
    # 【跨轮可选】当前活动流程的标识；没有活动流程时为 None。
    active_flow_id: str | None
    # 【跨轮可选】需要用户确认后才能执行的插件切换。
    pending_switch: dict[str, Any] | None
    # 【流程工作副本】当前流程已完成的业务动作，供组合业务避免重复执行。
    completed_actions: list[dict[str, Any]]

    # ===== 当前流程的恢复控制 =====
    # 【流程工作副本】连续未理解或当前插件无法处理的次数；匹配有效业务动作后清零。
    unrecognized_count: int

    # ===== 插件理解结果（单轮临时） =====
    # 【必需】understand 节点返回的插件内意图，decide 用它查动作表。
    intent: str
    # 【本轮需要】本轮模型新抽取的槽位；action handler 会按需与 context 合并。
    slots: dict[str, Any]
    # 【流程工作副本】已累积的槽位和 API 结果，例如 order_no/amount/merchant。
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
    # 【本轮需要】业务函数返回的结果，供回复节点填入话术并更新已知信息。
    action_result: ActionResult
    # 【最终输出】对用户的回复文本。
    reply: str
