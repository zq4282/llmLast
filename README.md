# 插件化对话引擎

这是一个 FastAPI + LangGraph 的最小可运行实现。系统对外只提供
`POST /api/chat`，内部所有业务共用一张五节点流程图：

```text
router → understand → decide → run_action → reply
```

退款、查询、停机保号和兜底聊天均为 YAML 插件。新增业务时只需在
`app/businesses/` 下增加 `plugin.yaml` 与 `handlers.py`，无需修改流程图。

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

不配置 LLM Key 时会使用内置规则进行路由和意图识别，便于本地开发及测试。
如需接入 DeepSeek 或其他 OpenAI 兼容接口，在 `.env` 中填写 `LLM_API_KEY`、
`LLM_BASE_URL` 与 `LLM_MODEL`。

## 测试

```bash
uv run pytest
```

`app/integrations/` 当前提供确定性的本地模拟实现。生产环境可保持函数契约不变，
替换为订单、退款、账户、短信和 LLM 的真实 HTTP/SDK 调用。

## Redis 会话状态

生产环境默认使用 Redis 保存跨轮会话状态，并按 `tenantId + sessionId` 隔离数据。
同一个会话的一整轮处理由 Redis 分布式锁串行化，状态每次写入后刷新 TTL。

```bash
REDIS_URL=redis://127.0.0.1:6379/0
SESSION_KEY_PREFIX=llmlast
SESSION_TTL_SECONDS=86400
SESSION_LOCK_TIMEOUT_SECONDS=30
SESSION_LOCK_BLOCKING_TIMEOUT_SECONDS=5
```

Redis 不可用时应用启动失败或聊天接口返回 503，不会静默降级到进程内存。
只有测试环境应设置 `SESSION_STORE_BACKEND=memory`。

## 连续未理解恢复

接口 `out` 固定为 `CHAT`、`REFUND`、`HUMAN`、`END` 四种，不接受其他值。
其中主动要求人工和连续三次未理解都返回 `HUMAN`。

模型连续返回 `unknown`，或返回当前插件状态未配置的意图时，引擎会保存跨轮计数并
保持当前业务状态。插件可通过 `<状态名小写>_<恢复阶段>` 形式的 fallback key 配置
状态化重问话术，例如 `confirm_refund_unknown_first`；未配置时使用引擎级通用话术。
第一次请用户按当前问题重新回答，第二次提示可转人工并预告升级，第三次返回
`out=HUMAN`，将会话标记为 `HANDOFF_PENDING`。转人工后的请求不会再次调用模型或
执行业务动作。只有匹配到有效业务动作后才会将连续恢复次数清零，业务接口异常不会
增加该次数。
