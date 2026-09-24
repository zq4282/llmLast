"""规定接口最终能返回哪些处理结果，供插件配置和接口共同使用。"""

from enum import StrEnum


# CHAT 是普通回复；REFUND 等表示业务动作结果，HUMAN 和 END 表示转人工或结束。
class DialogueOutput(StrEnum):
    CHAT = "CHAT"
    REFUND = "REFUND"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    REFUND_UNSUBSCRIBE = "REFUND_UNSUBSCRIBE"
    HUMAN = "HUMAN"
    END = "END"


ALLOWED_OUTPUTS = frozenset(item.value for item in DialogueOutput)
OUTPUT_NAMES = frozenset(DialogueOutput.__members__)
TERMINAL_OUTPUTS = frozenset({DialogueOutput.HUMAN, DialogueOutput.END})
