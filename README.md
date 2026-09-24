# 插件化对话引擎

这是一个 FastAPI + LangGraph 的最小可运行实现。系统对外只提供
`POST /api/chat`，内部所有业务共用一张五节点流程图：

```text
router → understand → decide → run_action → reply
```

退款和转人工流程均为 YAML 插件。新增业务时只需在
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

`plugin.yaml` 中 `do` 配置的业务动作统一实现在同插件目录的 `handlers.py` 中，
不再通过额外的业务接口文件转调。生产环境可在这些 Handler 内按需接入真实服务。

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

接口 `out` 固定为 `CHAT`、`REFUND`、`HUMAN`、`END` 四种，不接受其他值。
`CHAT` 和 `REFUND` 会继续保留当前会话上下文；`HUMAN` 和 `END` 是终态，本轮回复
返回后立即删除当前租户、当前 `sessionId` 的业务状态、上下文、历史和恢复计数。
其中主动要求人工和恢复步骤最终升级都返回 `HUMAN`。

模型连续返回 `unknown`，或返回当前插件状态未配置的意图时，引擎会保存跨轮计数并
保持当前业务状态。恢复次数不单独配置，而是由插件 `recovery.unknown` 和
`recovery.unsupported` 的步骤数量决定；每一步必须同时声明 `reply` 和 `out`，最后
一步必须为 `out: HUMAN`。因此配置两步就只需两条对应话术，配置三步就需要三条。
插件可通过 `<状态名小写>_<reply>` 形式的 fallback key 覆盖某个状态的话术，例如
`confirm_refund_unknown_first`；未配置时使用引擎级通用话术。只有匹配到有效业务动作
后才会将连续恢复次数清零，业务接口异常不会增加该次数。终态后的下一次请求按新会话
处理；内置前端会自动生成新的 `sessionId`，并且不再携带旧 `historyContext`。
