"""集中放引擎自己用的固定名称，业务规则和回复内容仍放在插件里。"""

from enum import StrEnum


# 一个业务可以正在办、暂时放下、已经办完，或被组合业务接替。
class FlowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


# 下面这些名称会写进会话；统一定义可避免读写时拼错字段名。
class FlowField(StrEnum):
    FLOW_ID = "flow_id"
    BUSINESS = "business"
    PLUGIN_STATE = "plugin_state"
    STATUS = "status"
    CONTEXT = "context"
    UNRECOGNIZED_COUNT = "unrecognized_count"
    COMPLETED_ACTIONS = "completed_actions"


class CompletedActionField(StrEnum):
    ACTION = "action"
    OUT = "out"
    ORDER_NO = "order_no"
    STATUS = "status"


class PendingSwitchField(StrEnum):
    SOURCE_FLOW_ID = "source_flow_id"
    TARGET_TASK = "target_task"
    TARGET_BUSINESS = "target_business"
    TRIGGER_MESSAGE = "trigger_message"


class NodeName(StrEnum):
    ROUTER = "router"
    UNDERSTAND = "understand"
    DECIDE = "decide"
    RUN_ACTION = "run_action"
    REPLY = "reply"


# 五个图节点和一轮对话的编排共用同一份状态字典。
class ChatField(StrEnum):
    SESSION_ID = "session_id"
    MESSAGE = "message"
    HISTORY = "history"
    TENANT_ID = "tenant_id"
    CALL_INFO = "call_info"
    SYSTEM_PROMPT = "system_prompt"
    CONFIG = "config"
    BUSINESS = "business"
    PLUGIN_STATE = "plugin_state"
    FLOWS = "flows"
    ACTIVE_FLOW_ID = "active_flow_id"
    PENDING_SWITCH = "pending_switch"
    COMPLETED_ACTIONS = "completed_actions"
    UNRECOGNIZED_COUNT = "unrecognized_count"
    INTENT = "intent"
    SLOTS = "slots"
    CONTEXT = "context"
    ACTION = "action"
    REPLY_KEY = "reply_key"
    NEXT_PLUGIN_STATE = "next_plugin_state"
    OUT = "out"
    HANDOFF_REASON = "handoff_reason"
    REPLY_SOURCE = "reply_source"
    SKIP_UNDERSTANDING = "skip_understanding"
    REROUTE_COUNT = "reroute_count"
    HANDLED_BY = "handled_by"
    ACTION_RESULT = "action_result"
    REPLY = "reply"


class ConfigField(StrEnum):
    MAX_REPLY_LEN = "max_reply_len"


class EngineIntent(StrEnum):
    OTHER = "other"
    UNKNOWN = "unknown"
    HUMAN = "human"
    CONFIRM_SWITCH = "confirm_switch"
    CANCEL_SWITCH = "cancel_switch"
    SWITCH_DENIED = "switch_denied"


class ActionName(StrEnum):
    NONE = "none"


class ActionResultField(StrEnum):
    OK = "ok"
    ERROR = "error"


class OtherDecisionType(StrEnum):
    ANSWER = "ANSWER"
    ROUTE = "ROUTE"


class OtherIntent(StrEnum):
    ASK_IDENTITY = "ask_identity"
    ASK_CAPABILITY = "ask_capability"
    ASK_COMPANY = "ask_company"
    CHITCHAT = "chitchat"


class SwitchDecisionType(StrEnum):
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    UNKNOWN = "UNKNOWN"


class HandoffReason(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    ACTION_FAILED = "ACTION_FAILED"
    CONSECUTIVE_UNRECOGNIZED = "CONSECUTIVE_UNRECOGNIZED"
    CONSECUTIVE_UNSUPPORTED = "CONSECUTIVE_UNSUPPORTED"


class RecoveryTrigger(StrEnum):
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class FallbackKey(StrEnum):
    END = "end"
    UNKNOWN = "unknown"
    UNKNOWN_FIRST = "unknown_first"
    UNKNOWN_SECOND = "unknown_second"
    UNKNOWN_HANDOFF = "unknown_handoff"
    HUMAN_HANDOFF = "human_handoff"
    UNSUPPORTED_FIRST = "unsupported_first"
    UNSUPPORTED_SECOND = "unsupported_second"
    UNSUPPORTED_HANDOFF = "unsupported_handoff"
    UNSUPPORTED_INTENT = "unsupported_intent"


SYSTEM_BUSINESS = "system"
OTHER_BUSINESS = "other"
OTHER_HANDLER_NAME = OTHER_BUSINESS
INTERNAL_ACTION_ERROR = "internal_error"
ACTION_SUCCESS_STATUS = "SUCCESS"
MAX_INTERNAL_REROUTES = 1
DEFAULT_MAX_REPLY_LEN = 60
DEFAULT_SYSTEM_PROMPT = "你是会员业务客服"
