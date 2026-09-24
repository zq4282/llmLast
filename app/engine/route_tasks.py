"""顶层路由 Task 的单一配置源。"""

from app.engine.outputs import DialogueOutput


ROUTE_TASK_UNKNOWN = "UNKNOWN"


ROUTE_TASK_DEFINITIONS = {
    DialogueOutput.REFUND_UNSUBSCRIBE.value: """同时明确要求退回已经扣除的费用，并停止订阅或后续自动续费。
  - *典型表达*：不要扣钱了下个月也不要扣了 / 这次扣的钱退给我以后也别扣了 / 退款并取消订阅 / 钱退回来下个月不要再续费 / 退钱再退订。
  - *边界*：必须同时包含“退回已扣费用”和“停止未来订阅/扣费”两个明确诉求；只有其中一个诉求时分别判定为 REFUND 或 UNSUBSCRIBE。""",
    DialogueOutput.REFUND.value: """明确要求退款、退费、撤销/退回已产生账单。
  - *典型表达*：退款 / 退钱（含ASR错字：退前、退狂） / 把198元退掉 / 取消这笔扣款。
  - *边界*：即使夹杂“没订过/不知道怎么开的/乱扣费”，只要有明确退款诉求但未要求停止未来订阅，判定为 REFUND。""",
    DialogueOutput.UNSUBSCRIBE.value: """明确要求退订、取消订阅或关闭后续自动续费，但不要求退回已经支付的费用。
  - *典型表达*：退订 / 取消订阅 / 关闭自动续费 / 下个月别再续了 / 不再订这个会员。
  - *边界*：只停止未来续费判定为 UNSUBSCRIBE；同时明确要求退回已扣费用时，判定为 REFUND_UNSUBSCRIBE。""",
    "BUSINESS_QA": """了解、解释、查询账单或业务规则，无退款或退订诉求。
  - *典型表达*：198是什么钱 / 为什么扣费 / 什么时候开的 / 自动续费规则是什么 / 会员权益没到账。
  - *边界*：仅表达“扣费质疑”（如“我没开过怎么扣了198”）但**未提及退款**，严禁推测其想退款，必须定为 BUSINESS_QA。""",
    DialogueOutput.HUMAN.value: """要求人工介入，或明确拒绝机器服务。
  - *典型表达*：转人工（含ASR错字：转仁工、抓人工） / 找人工客服 / 叫你们主管来 / 别跟我说了叫真人 / 投诉 / 市长热线 / 12326投诉 / 骂人 / 骗子 / 傻逼""",
    ROUTE_TASK_UNKNOWN: """意图模糊、信息缺失严重、或单纯无实质业务指向的应答。
  - *典型表达*：帮我处理下 / 这个怎么弄 / 不行 / 知道了 / 喂喂喂。""",
}

ROUTE_TASKS = frozenset(ROUTE_TASK_DEFINITIONS)


def render_route_task_definitions() -> str:
    return "\n".join(
        f"- **{task}**：{description}"
        for task, description in ROUTE_TASK_DEFINITIONS.items()
    )


def render_route_task_options() -> str:
    return " | ".join(ROUTE_TASK_DEFINITIONS)
