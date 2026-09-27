"""构建各场景的模型消息；只拼装提示词，不请求模型或解析结果。"""

import json
from typing import Any

from app.engine.loader import Plugin
from app.engine.route_tasks import render_route_task_definitions, render_route_task_options


# 顶层只选业务，不查询订单，也不直接决定办理结果。
TASK_ROUTER_PROMPT = """# Role

语音客服系统的【任务路由识别器 Task Router】。
唯一职责：分析用户当前的表达（必要时参考上一轮对话上下文），将其分类映射至唯一的预设 Task。仅负责分类识别，不执行业务逻辑。

## 一、数据特性（ASR 文本容错）

当前输入由语音识别（ASR）直接转写生成，**可能存在同音错别字、口语吞音、连字、无标点或无意义语气词**（例：“退前”->“退钱”、“退狂”->“退款”、“转仁工”->“转人工”）。请结合语境及发音容错还原真实含义，切勿仅因错别字直接归为 UNKNOWN。

## 二、核心判定原则

1. **当前句优先**：若用户当前意图明确，直接判定，不回溯历史。
2. **上下文按需补全**：仅当用户表述为代词指代（如“就这个”、“弄它”）或短促确认（如“对”、“可以”、“赶紧办”）时，才参考上一轮客服播报补全语义；结合后仍指向不明则归为 UNKNOWN。
3. **诉求优先于情绪与原因**：
   - 情绪（愤怒、催促、不满）不代表具体意图，严禁因情绪推断任务。
   - 复合诉求中，以“最终动作落脚点”为主任务（例：“不知道怎么扣的，赶紧退我钱” -> 主诉求为退款，判定为 REFUND）。
4. **无明确任务指向的弱应答**：若用户仅输入“好的”、“行”、“对”，且上一轮并未发起与特定 Task 相关的意图确认，统一判定为 UNKNOWN。

## 三、Task 枚举与边界

__TASK_DEFINITIONS__

## 四、禁止事项

- 严禁执行查询、退款等业务动作或给出解答建议。
- 严禁把“质疑扣费/表达不满”直接等同于“要求退款”。
- 严禁在 JSON 之外输出任何解释、分析或前置后置文字。

## 五、输出格式

必须严格仅输出标准 JSON 格式。`confidence` 为 **0.0 到 1.0 之间的浮点数（保留两位小数）**：

- **0.85 ~ 1.00**：意图极明确、关键词完备（如包含明确动词且无歧义）。
- **0.60 ~ 0.84**：依赖上下文补全、存在轻度 ASR 谐音推断，或表达略显口语化但主体明确。
- **0.00 ~ 0.59**：语义严重缺失、多意图混杂冲突、或归入 UNKNOWN。
```json
{
  "task": "__TASK_OPTIONS__",
  "confidence": 0.95
}
```""".replace(
    "__TASK_DEFINITIONS__", render_route_task_definitions()
).replace(
    "__TASK_OPTIONS__", render_route_task_options()
)


def _render_prompt(template: str, replacements: dict[str, str]) -> str:
    """只替换声明的占位符，保留提示词示例中的 JSON 花括号。"""

    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def build_route_messages(message: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    """顶层路由带上完整历史，并单列最近客服播报以补全短句。"""

    previous_assistant = next(
        (item.get("content", "") for item in reversed(history) if item.get("role") == "assistant"),
        "",
    )
    user_input = json.dumps(
        {"历史对话": history, "上一轮客服播报": previous_assistant, "当前用户表达": message},
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": TASK_ROUTER_PROMPT},
        {"role": "user", "content": user_input},
    ]


def build_understand_messages(
    message: str,
    plugin: Plugin,
    plugin_state: str,
    context: dict[str, Any],
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """业务理解带上当前进度、已知信息和完整对话历史。"""

    history_text = "\n".join(
        f"{'系统' if item.get('role') == 'assistant' else '用户'}：{item.get('content', '')}"
        for item in history
    ) or "（无）"
    prompt = _render_prompt(plugin.prompt, {
        "{state}": plugin_state,
        "{context}": json.dumps(context, ensure_ascii=False),
        "{history}": history_text,
        "{user_input}": message,
    })
    return [{"role": "user", "content": prompt}]


def build_other_messages(
    message: str,
    plugin: Plugin,
    available_tasks: dict[str, str],
    *,
    system_prompt: str,
    max_reply_len: int,
) -> list[dict[str, str]]:
    """other 只接收本轮消息和可转入业务，不带原业务进度。"""

    task_names = " | ".join(available_tasks) or "（无）"
    task_definitions = "\n".join(
        f"- {task}：{description}" for task, description in available_tasks.items()
    ) or "（当前没有可转入的固定业务）"
    prompt = _render_prompt(plugin.prompt, {
        "{system_prompt}": system_prompt,
        "{available_tasks}": task_names,
        "{task_definitions}": task_definitions,
        "{user_input}": message,
        "{max_reply_len}": str(max_reply_len),
    })
    return [{"role": "user", "content": prompt}]


def build_switch_messages(message: str) -> list[dict[str, str]]:
    """切换确认只判断当前回答是否同意或取消切换。"""

    prompt = f"""你是业务流程切换确认器。用户上一轮被询问是否暂停当前业务并切换到另一个业务。
只判断用户当前回答：确认切换、取消切换，还是没有明确回答。
用户当前说：{message}
只输出 JSON：{{"decision":"CONFIRM|CANCEL|UNKNOWN"}}"""
    return [{"role": "user", "content": prompt}]
