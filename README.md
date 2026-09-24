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
uv run python run.py
```

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
替换为订单、退款、账户、短信和 LLM 的真实 HTTP/SDK 调用；会话存储也可按
`MemorySessionStore` 接口替换为 Redis。
