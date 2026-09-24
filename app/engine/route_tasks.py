"""顶层路由 Task 的单一配置源。"""

from app.engine.outputs import DialogueOutput


ROUTE_TASK_REFUND = DialogueOutput.REFUND.value
ROUTE_TASK_UNSUBSCRIBE = DialogueOutput.UNSUBSCRIBE.value
ROUTE_TASK_REFUND_UNSUBSCRIBE = DialogueOutput.REFUND_UNSUBSCRIBE.value
ROUTE_TASK_BUSINESS_QA = "BUSINESS_QA"
ROUTE_TASK_HUMAN = DialogueOutput.HUMAN.value
ROUTE_TASK_UNKNOWN = "UNKNOWN"


ROUTE_TASK_DEFINITIONS = {
    ROUTE_TASK_REFUND_UNSUBSCRIBE: """同时明确要求退回已经扣除的费用，并停止订阅或后续自动续费。
  - *典型表达*：不要扣钱了下个月也不要扣了 / 这次扣的钱退给我以后也别扣了 / 退款并取消订阅 / 钱退回来下个月不要再续费 / 退钱再退订。
  - *边界*：必须同时包含“退回已扣费用”和“停止未来订阅/扣费”两个明确诉求；只有其中一个诉求时分别判定为 REFUND 或 UNSUBSCRIBE。""",
    ROUTE_TASK_REFUND: """明确要求退款、退费、撤销或退回已产生账单。
  - *典型表达*：退款 / 退钱（含 ASR 错字：退前、退狂） / 把198元退掉 / 取消这笔扣款。
  - *边界*：即使夹杂“没订过/不知道怎么开的/乱扣费”，只要有明确退款诉求但未要求停止未来订阅，判定为 REFUND。""",
    ROUTE_TASK_UNSUBSCRIBE: """明确要求退订、取消订阅或关闭后续自动续费，但不要求退回已经支付的费用。
  - *典型表达*：退订 / 取消订阅 / 关闭自动续费 / 下个月别再续了 / 不再订这个会员。
  - *边界*：只停止未来续费判定为 UNSUBSCRIBE；同时明确要求退回已扣费用时，判定为 REFUND_UNSUBSCRIBE。""",
    ROUTE_TASK_BUSINESS_QA: """了解、解释或查询账单与业务规则，无退款或退订诉求。
  - *典型表达*：198是什么钱 / 为什么扣费 / 什么时候开的 / 自动续费规则是什么 / 会员权益没到账。
  - *边界*：仅表达“扣费质疑”（如“我没开过怎么扣了198”）但**未提及退款**，严禁推测其想退款，必须定为 BUSINESS_QA。""",
    ROUTE_TASK_HUMAN: """要求人工介入，或明确拒绝机器服务。
  - *典型表达*：转人工（含 ASR 错字：转仁工、抓人工） / 找人工客服 / 叫你们主管来 / 别跟我说了叫真人 / 投诉 / 市长热线 / 12326投诉 / 骂人 / 骗子 / 傻逼。
  - *边界*：明确要求真人、投诉升级或出现辱骂时判定为 HUMAN；仅表达不满但仍有明确业务诉求时，优先判定对应业务 Task。""",
    ROUTE_TASK_UNKNOWN: """不属于上述固定业务的所有表达，包括身份、能力、公司主体、闲聊，以及意图模糊或信息缺失严重的内容。
  - *典型表达*：你是谁 / 你能做什么 / 你们是哪家公司 / 今天天气怎么样 / 讲个笑话 / 帮我处理下 / 喂喂喂。
  - *边界*：只要没有明确命中其他固定 Task，就归入 UNKNOWN，交给无状态 other 插件回答或进行二级识别。""",
}

ROUTE_TASKS = frozenset(ROUTE_TASK_DEFINITIONS)


ROUTE_TASK_LABELS = {
    ROUTE_TASK_REFUND: "退款流程",
    ROUTE_TASK_UNSUBSCRIBE: "退订流程",
    ROUTE_TASK_REFUND_UNSUBSCRIBE: "退款并退订流程",
    ROUTE_TASK_BUSINESS_QA: "业务咨询",
    ROUTE_TASK_HUMAN: "人工服务",
    ROUTE_TASK_UNKNOWN: "其他问题",
}


def render_route_task_definitions() -> str:
    return "\n".join(
        f"- **{task}**：{description}"
        for task, description in ROUTE_TASK_DEFINITIONS.items()
    )


def render_route_task_options() -> str:
    return " | ".join(ROUTE_TASK_DEFINITIONS)
