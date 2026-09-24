"""对外对话结果常量的单一配置源。"""

from enum import StrEnum


class DialogueOutput(StrEnum):
    CHAT = "CHAT"
    REFUND = "REFUND"
    HUMAN = "HUMAN"
    END = "END"


ALLOWED_OUTPUTS = frozenset(item.value for item in DialogueOutput)
OUTPUT_NAMES = frozenset(DialogueOutput.__members__)
