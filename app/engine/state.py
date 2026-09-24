"""LangGraph 在五个节点之间传递的状态。"""

from typing import Any, TypedDict

from app.engine.action_result import ActionResult
from app.engine.outputs import DialogueOutput


class CallInfoState(TypedDict):
    """一次通话的共享元数据，字段名统一使用后端 snake_case。"""

    caller: str
    callee: str
    call_start_time: str


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
    # 【单轮可选】调用方传入的系统提示词及生成配置，供后续回复节点扩展使用。
    system_prompt: str
    config: dict[str, Any]

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

    # ===== 会话恢复控制（跨轮共享） =====
    # 连续语义未理解次数；识别出有效意图后立即清零。
    unrecognized_count: int
    # BOT/HANDOFF_PENDING/HUMAN/ENDED；人工接管后禁止再次进入机器人流程。
    conversation_status: str
    # 转人工原因，例如 USER_REQUESTED/CONSECUTIVE_UNRECOGNIZED。
    handoff_reason: str | None
    # 人工系统返回的接管标识，尚未对接时为空。
    handoff_id: str | None

    # ===== 插件理解结果（单轮临时） =====
    # 【必需】understand 节点返回的插件内意图，decide 用它查动作表。
    intent: str
    # 【可选】插件内意图置信度，仅用于观测，删除不影响流程。
    intent_confidence: float
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
    # 【必需】actions.out，对外标识 CHAT/REFUND/HUMAN/END。
    out: DialogueOutput
    # 【内部需要】区分 templates 和 fallbacks 话术来源。可通过拆分 reply_key 类型后移除。
    use_fallback: bool
    # 【单轮临时】True 表示使用引擎级恢复话术，不依赖具体业务插件。
    use_system_fallback: bool
    # 【单轮临时】Router 已直接产出 unknown/human 时跳过插件理解。
    skip_understanding: bool

    # ===== 执行与回复（单轮临时/最终输出） =====
    # 【本轮需要】run_action 调用业务 API 后的结果，reply 用于填充话术和更新 context。
    action_result: ActionResult
    # 【最终输出】对用户的回复文本。
    reply: str
    # 【可选】内部错误详情，用于日志/排查；不应直接返回给用户。
    error: str | None
