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

工具定义位于 `agent_mvp.py` 的 `ToolSpec` 中，输入契约由 `SEARCH_ITEMS_SCHEMA` 和 `GET_ITEM_SCHEMA` 表示。当前验证的 Schema 关键字包括：

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

## 当前代码与测试

```text
agent_mvp.py
    ToolSpec、ToolCall、TraceEvent
    ToolRegistry
    search_items 和 get_item 工具
    OfflineQueryAgent

test_agent_mvp.py
    工具契约
    真实数据库查询
    参数错误
    权限拒绝
    未知工具
    商品不存在
    超时
    trace
    两种自然语言查询路由
```

`OfflineQueryAgent` 是确定性的离线替身，不代表真实大模型。它只支持当前两个演示查询形式，用来先验证工具边界。后续接入真实模型时，需要继续验证模型选择工具和生成参数的正确率。

## 当前验证结果

```text
test_agent_mvp.py：10 passed
```

当前还没有完成：

```text
真实模型接入
Tool Calling Agent
RAG 检索
MCP Server/Client
Agent 评测 Harness
```

面试官最希望听到的标准答案：

> 我先限定一个商品查询任务，开放商品搜索和商品详情两个只读工具，并为它们定义输入、输出、权限和错误 Schema。Agent 不能直接执行任意函数，而是只能从白名单选择工具；调用必须经过参数校验、权限校验和超时控制，并记录 trace、状态、耗时和错误类型。当前先用确定性的离线 Agent 验证工具边界，再接入真实模型、RAG 和 MCP，避免把模型不确定性与工具实现问题混在一起。
