# Agent 评测 MVP 设计

## 学习目标

本阶段先实现一个确定性的 Agent 工具调用边界，不接真实大模型，不执行系统命令，也不引入 MCP。目标是验证：

```text
用户任务
→ 工具选择
→ 结构化参数
→ Schema 校验
→ 权限校验
→ 业务工具执行
→ 超时和错误处理
→ trace 记录
```

这属于后端工程与 AI 应用工程的交叉内容。对测开而言，重点是把 Agent 当成待测系统，验证工具选择、参数契约、权限边界、超时行为和回归证据。

## 当前业务任务

限定一个任务：

> 根据用户的自然语言需求查询商品。

复用现有 SQLite 商品数据库和查询能力，不重新实现 FastAPI 路由。

## 工具契约

当前只开放两个只读工具：

| 工具 | 参数 | 权限 | 结果 |
| --- | --- | --- | --- |
| `search_items` | `keyword: string/null`、`max_price: number/null` | `catalog:read` | 商品列表和数量 |
| `get_item` | `item_id: integer` | `catalog:read` | 单个商品详情 |

工具定义位于 `app/agent/mvp.py` 的 `ToolSpec` 中，输入契约由 `SEARCH_ITEMS_SCHEMA` 和 `GET_ITEM_SCHEMA` 表示。当前验证的 Schema 关键字包括：

```text
type
properties
required
additionalProperties
```

暂时没有引入额外的 JSON Schema 第三方库，因为当前只有两个工具，项目只需要这几个规则。后续工具数量和 Schema 复杂度增加时，再评估是否使用专门的校验库。

## 三个核心数据结构

### `ToolSpec`

描述一个可被发现和调用的工具，包括名称、说明、输入 Schema、所需权限、处理函数和超时。

### `ToolCall`

描述一次具体调用：

```text
tool_name   调用哪个工具
arguments   传给工具的结构化参数
trace_id    属于哪一次 Agent 任务
permissions 当前调用者拥有的权限
```

`dataclass` 在这里的作用是把这些字段固定成一个结构，避免在不同函数之间随意传递没有约束的字典。

### `TraceEvent`

记录一次调用是否成功、耗时和错误类型，当前字段包括：

```text
trace_id
tool_name
status
duration_ms
error_type
```

## 执行边界

`ToolRegistry.execute()` 固定按下面顺序执行：

```text
工具白名单
→ 权限校验
→ 参数 Schema 校验
→ 单次调用超时
→ 业务处理函数
→ 结构化结果和 trace
```

错误类型目前统一为：

```text
TOOL_NOT_FOUND      工具不在白名单
PERMISSION_DENIED   缺少 catalog:read 权限
INVALID_ARGUMENT    参数缺失、类型错误或未知参数
ITEM_NOT_FOUND      商品业务查询没有命中
TIMEOUT             工具超过执行时限
TOOL_ERROR          未预期的工具内部错误
```

权限和参数检查失败时，业务处理函数不会执行。超时返回后，调用结果也不会被当成成功结果使用。

## Tool Calling 分层

当前代码已经把 Agent 拆成两层：

```text
Planner
    负责理解用户任务并生成 ToolCall

ToolCallingAgent
    负责把 ToolCall 交给 ToolRegistry

ToolRegistry
    负责白名单、权限、Schema、超时和实际执行
```

当前的 `OfflineQueryPlanner` 用规则模拟模型。以后接入真实模型时，只替换 Planner：

```text
规则 Planner
离线 Stub Planner
真实模型 Planner
        ↓ 都生成 ToolCall
同一个 ToolCallingAgent
        ↓
同一个 ToolRegistry
```

这样模型只能提出调用意图，不能绕过程序直接执行 Python 函数。测开可以分别验证 Planner 的工具选择，以及 Executor 的安全边界。

项目现已增加 `app/agent/rag.py`，把现有 RAG 门禁注册为第三个只读工具：

```text
search_items       catalog:read     查询商品列表
get_item           catalog:read     查询单个商品
search_knowledge   knowledge:read   检索规则知识
```

`OfflineCatalogKnowledgePlanner` 使用确定性关键词完成商品问题与知识问题分流，继续作为
稳定、快速的离线替身。知识工具会复用 Agent 生成的 `trace_id`，但拒答时不会把低相关
候选内容交给后续模型；真实 Qwen 只替换 Planner，ToolRegistry 的白名单、Schema、
权限和超时边界保持不变。

## 真实 Qwen Planner

`app/agent/ollama.py` 已经把本机 `qwen3:4b-instruct` 接到相同的 `ToolPlanner`
接口。它通过 Ollama `/api/chat` 的原生 `tools` 和 `message.tool_calls` 完成一次工具选择：

```text
用户自然语言
→ OllamaToolPlanner向Qwen提供三个工具Schema
→ Qwen返回一个tool_call
→ Planner只提取tool_name和arguments
→ parse_tool_call由应用注入permissions与trace_id
→ ToolRegistry重新检查白名单、权限、Schema和超时
→ 执行真实工具
```

模型只拥有“提出计划”的能力。工具处理函数、调用者权限和 `trace_id` 不会发送给模型；
即使模型编造工具、生成坏参数或选择了调用者无权使用的工具，最终也会被程序拒绝。
当前 MVP 每轮只允许一个工具调用，多工具并行或多轮工具循环留到后续扩展。

普通测试通过替换 HTTP 传输函数注入 Ollama 响应，不依赖服务和模型；真实模型测试使用
`RUN_OLLAMA_INTEGRATION=1` 显式启用，避免普通回归和 CI 因本地服务状态而不稳定。

### 原始模型输出的二次解析

模型适配器通常返回字典或 JSON，而不是项目内部的 `ToolCall`。`parse_tool_call()` 负责把它转换为受控对象：

```text
检查顶层对象
→ 只允许 tool_name 和 arguments
→ 检查工具名称是非空字符串
→ 检查 arguments 是对象
→ 由应用程序注入权限和 trace_id
→ 创建 ToolCall
```

模型不能自行提交 `permissions`、`trace_id` 或其他执行控制字段。即使模型输出看起来是合法 JSON，也必须经过这一步解析，再交给 `ToolRegistry` 做工具白名单和参数 Schema 校验。

## 评测 Harness

`EvaluationCase` 将一条 Agent 评测用例固定为输入、权限和预期结果；`evaluate_case()` 会分别检查：

```text
工具名称
工具参数
成功/失败状态
错误类型
trace_id
指定的返回数据、内容关键字与RAG内部trace一致性
```

`evaluate_cases()` 可以按顺序运行一批用例，后续还可以在不改变用例格式的情况下，替换 Planner、比较不同 Prompt 或接入真实模型。当前评测使用确定性断言，不使用 LLM Judge。

`app/agent/evaluation.py` 从 `eval_cases/agent_rag_v1.json` 加载15条版本化任务，
复用 `evaluate_case()` 并增加处理函数调用次数检查、聚合指标和JSON/Markdown报告。
`end_to_end_ms` 记录Planner到Registry结果的耗时，不再把工具trace耗时当作完整链路耗时。
默认使用固定依赖，不代表真实语义检索质量；人工复核初始为待完成。
运行方式和证据边界见 [Agent与RAG版本化评测说明](Agent与RAG版本化评测说明.md)。

## 当前代码与测试

```text
app/agent/mvp.py
    ToolSpec、ToolCall、TraceEvent
    ToolRegistry
    search_items 和 get_item 工具
    OfflineQueryAgent

tests/agent/test_agent_mvp.py
    工具契约
    真实数据库查询
    参数错误
    权限拒绝
    未知工具
    商品不存在
    超时
    trace
    两种自然语言查询路由

app/agent/ollama.py
    Ollama原生Tool Calling请求
    模型tool_calls响应解析
    规划阶段超时、连接失败和异常响应映射

tests/agent/test_agent_ollama.py
    工具Schema转换
    权限与trace不发送给模型
    白名单、权限和坏参数二次防线
    无工具、多工具、超时和服务不可用

tests/agent/test_agent_ollama_integration.py
    真实Qwen商品工具选择
    真实Qwen知识工具选择
```

`OfflineQueryAgent` 是确定性的离线替身，不代表真实大模型。它只支持当前两个演示查询形式，用来先验证工具边界。后续接入真实模型时，需要继续验证模型选择工具和生成参数的正确率。

## 当前验证结果

`tests/agent/test_agent_mvp.py` 与 `tests/agent/test_agent_rag.py` 分别覆盖基础工具边界和 Agent-RAG 分流；实际
通过数量以当前测试命令输出为准，不在文档中长期保存容易过期的固定数字。

当前还没有完成：

```text
多轮或并行Tool Calling循环
MCP Server/Client
Prompt A/B比较与评测指标CI退化门禁
```

2026-08-31阶段记录：用户已运行15条版本化评测，离线与真实Qwen Planner两轮均通过，
每轮选定的5条复核项已按用户反馈补录。详细指标、查询改写和固定依赖的证据边界见
[Agent与RAG评测人工复核记录](Agent与RAG评测人工复核记录_20260831.md)。

面试官最希望听到的标准答案：

> 我先用确定性的离线 Planner 验证工具白名单、参数、权限、超时和 trace，再通过 Ollama 原生 Tool Calling 接入 Qwen。模型只负责选择工具和生成参数，应用程序不会把权限和执行权交给模型；模型输出还要经过解析、Schema、白名单和权限二次校验。普通回归使用可注入的假传输保持稳定，真实模型行为由显式开启的集成测试验证。
