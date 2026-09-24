"""顶层路由 Task 的单一配置源。"""

from app.engine.outputs import DialogueOutput


ROUTE_TASK_UNKNOWN = "UNKNOWN"


ROUTE_TASK_DEFINITIONS = {
    DialogueOutput.REFUND.value: """明确要求退款、退费、撤销/退回已产生账单。
  - *典型表达*：退款 / 退钱（含ASR错字：退前、退狂） / 把198元退掉 / 取消这笔扣款。
  - *边界*：即使夹杂“没订过/不知道怎么开的/乱扣费”，只要最终有明确退款诉求，均判定为 REFUND。""",
    "BUSINESS_QA": """了解、解释、查询账单或业务规则，无退款诉求。
  - *典型表达*：198是什么钱 / 为什么扣费 / 什么时候开的 / 怎么取消自动续费 / 会员权益没到账。
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
