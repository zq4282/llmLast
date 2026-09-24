# 插件化对话引擎

这是一个 FastAPI + LangGraph 的插件化对话引擎。系统对外只提供
`POST /api/chat`，固定业务共用一张五节点流程图：

```text
router → understand → decide → run_action → reply
```

退款、退订、退款并退订和转人工流程均为 YAML workflow 插件。新增业务时在
`route_tasks.py` 登记顶层 Task，并在 `app/businesses/` 下增加 `plugin.yaml`、
`prompt.md` 与 `handlers.py`；共享流程图无需修改。

## 引擎代码结构

`app/engine/` 的核心对象各负责一件事：

- `WorkflowGraph`：只定义五节点的固定拓扑，以及 `other` 的退出位置。
- `RouterNode`、`UnderstandNode`、`DecisionNode`、`ActionNode`、`ReplyNode`：
  分别处理单轮的路由、理解、决策、执行和回复。
- `DialogueEngine`：串起一轮请求，处理 `other` 插话和业务切换。
- `FlowManager`：负责跨轮流程快照的创建、恢复、切换和完成。

`constants.py` 集中维护固定的状态字段、流程状态和引擎内部取值；
`loader.py` 负责加载和校验插件，`llm.py` 负责模型请求，`state.py` 定义图状态。
常规业务轮次由 `DialogueEngine` 调用共享图；识别到 `other` 时，图在 `understand`
后返回，由 `DialogueEngine` 处理插话或切换。

图拓扑、状态协议、流程生命周期和对外输出属于稳定的引擎规则；不同业务的状态、
意图、动作、提示词及话术放在 `app/businesses/` 的插件目录中；顶层 Task
定义放在 `route_tasks.py`。新增业务不增加新的引擎分支或节点类。

每个业务目录用 `plugin.yaml` 定义状态与动作，用 `prompt.md` 保存模型提示词，并由
`prompt_file: prompt.md` 引用；加载器也兼容旧的内联 `prompt`。退款、退订及组合业务
共用的订单查询放在 `app/services/order_lookup.py`，各插件通过自己的 `handlers.py`
暴露 `query_order` 动作。

`other` 是单独的无状态 overlay 插件。顶级路由未匹配固定业务，或者活动业务插件
返回 `other` 时，引擎临时调用它。它可以直接生成身份、能力、公司主体或闲聊回答，
也可以发出内部 `ROUTE` 信号，将同一条原始消息交给固定业务插件。other 自己不进入
会话流程列表、不保存 `plugin_state`，真正回答时对外固定返回 `CHAT`。

## 插话与插件切换

每个 workflow 插件默认通过 `global_handlers.other` 接入 other。状态可配置：

```yaml
state_policies:
  CONFIRM_REFUND:
    switch_policy: confirm       # allow | confirm | deny
    remind_after_other: true
    reminder: "刚才的退款还在等待确认，请回复“退款”或“不退款”。"
```

- other 返回 `ANSWER`：引擎返回 `CHAT`，ACTIVE 业务状态保持不变；提醒由引擎追加，
  other 看不到业务状态或上下文。
- other 返回 `ROUTE`：每轮最多内部重路由一次。`allow` 直接切换，`confirm` 保存
  `pending_switch` 等待下一轮确认，`deny` 保持当前流程。
- 普通切换将旧流程标记为 `SUSPENDED`；退款或退订升级到退款并退订时标记为
  `SUPERSEDED`，并复用兼容上下文和已完成动作，防止重复办理。
- 活动业务中的 `human` 仍由当前插件处理；只有没有活动业务时，顶级 HUMAN 才进入
  独立 human 插件。

会话通过 `flows` 保存流程快照，状态包含 `ACTIVE`、`SUSPENDED`、`COMPLETED`、
`CANCELLED` 和 `SUPERSEDED`。兼容字段 `business/plugin_state/context` 始终投影当前
ACTIVE 流程，旧客户端无需修改。

## 订单查询失败后的处理

退款、退订和退款并退订都使用同一套有限重试规则：首次查询失败进入
`ASK_ORDER_INFO`，请用户补充手机号或订单号；补充后仍失败进入
`RETRY_ORDER_INFO`，再给一次核对机会。最后一次仍失败时返回 `HUMAN`，
关闭当前活动流程，由人工继续核实。查询成功则正常进入业务确认。
用户明确无法提供信息时直接转人工；明确取消或结束时返回 `END`。

## 运行

```bash
uv sync
redis-server
uv run python run.py
```

可以将 `.env.example` 复制为 `.env` 后修改 Redis 和 LLM 配置。

接口文档：<http://127.0.0.1:8000/docs>

前端测试台：<http://127.0.0.1:8000/>。测试台支持多轮对话、会话 ID 切换、
业务路由检查、原始 JSON 查看，以及 `⌘/Ctrl + K` 快速填入测试指令。

## 调用示例

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "sessionId": "CALL_001",
    "tenantId": 1002,
    "callInfo": {
      "caller": "13800138000",
      "callee": "10000",
      "callStartTime": "2026-09-22 10:00:00"
    },
    "systemPrompt": "你是会员业务客服，请识别用户意图并生成回复话术",
    "historyContext": [],
    "currentUserText": "我要退款",
    "config": {"maxReplyLen": 60, "temperature": 0.1}
  }'
```

除 `currentUserText` 外均提供默认值；前端测试台会自动生成呼叫时间与历史上下文。
`callInfo` 在后端作为独立的跨轮共享状态保存，图节点和业务 Handler 可通过
`state["call_info"]` 读取；它不会混入业务槽位 `context`。

路由、业务理解和 other 问答都依赖模型。不配置 LLM Key 时接口会明确返回模型配置错误。
如需接入 DeepSeek 或其他 OpenAI 兼容接口，在 `.env` 中填写 `LLM_API_KEY`、
`LLM_BASE_URL` 与 `LLM_MODEL`。

## 测试

```bash
uv run pytest
```

`plugin.yaml` 中 `do` 配置的业务动作统一由同插件目录的 `handlers.py` 暴露。
共用能力放在 `app/services/`，生产环境可在这些服务中接入真实接口。

## Redis 会话状态

生产环境默认使用 Redis 保存跨轮会话状态，并按 `tenantId + sessionId` 隔离数据。
状态每次写入后刷新 TTL；请求本身不在本服务内加锁。

```bash
REDIS_URL=redis://127.0.0.1:6379/0
SESSION_KEY_PREFIX=llmlast
SESSION_TTL_SECONDS=86400
```

Redis 不可用时应用启动失败或聊天接口返回 503，不会静默降级到进程内存。
只有测试环境应设置 `SESSION_STORE_BACKEND=memory`。

## 连续未理解恢复

接口 `out` 固定为 `CHAT`、`REFUND`、`UNSUBSCRIBE`、`REFUND_UNSUBSCRIBE`、`HUMAN`、`END` 六种，不接受其他值。
`CHAT`、`REFUND`、`UNSUBSCRIBE` 和 `REFUND_UNSUBSCRIBE` 会继续保留当前活动流程；
`HUMAN` 和 `END` 会将当前流程标记为 `COMPLETED`，但不会删除历史流程和对话历史。
完成后不再锁定路由，同一 `sessionId` 的下一条消息可以重新进入顶级路由。
其中主动要求人工和恢复步骤最终升级都返回 `HUMAN`。

模型连续返回 `unknown`，或返回当前插件状态未配置的意图时，引擎会保存跨轮计数并
保持当前业务状态。恢复次数不单独配置，而是由插件 `recovery.unknown` 和
`recovery.unsupported` 的步骤数量决定；每一步必须同时声明 `reply` 和 `out`，最后
一步必须为 `out: HUMAN`。因此配置两步就只需两条对应话术，配置三步就需要三条。
插件可通过 `<状态名小写>_<reply>` 形式的 fallback key 覆盖某个状态的话术，例如
`confirm_refund_unknown_first`；未配置时使用引擎级通用话术。只有匹配到有效业务动作
后才会将连续恢复次数清零，业务接口异常不会增加该次数。终态后的流程保留为历史快照，
下一次请求在没有 ACTIVE 流程的情况下重新执行顶级路由。
