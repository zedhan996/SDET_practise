# MCP 最小工具调用

## 本轮目标

MCP（Model Context Protocol，模型上下文协议）提供标准化的工具发现与调用方式。
第一轮只暴露已有的 `get_item`，先验证独立客户端能发现工具、读取 Schema 并查询商品。

```text
app/mcp/client.py
    → 标准输入/输出管道（stdio），跨进程传递 MCP 消息
app/mcp/server.py
    → ToolRegistry 校验权限、参数并执行
原有 get_item_tool
    → 应用库 data/app/dev.db
```

客户端自动启动并关闭服务端子进程，无需手工另开服务窗口，没有 HTTP 监听端口。
本轮不调用 Ollama、RAG 或 Planner；客户端在代码中明确指定工具名称和参数。
以后 Planner 可以先提出调用，再由 MCP 客户端发送给服务端，两者职责不同。

## 文件职责

- `app/mcp/server.py`：只将 `get_item` 注册为 MCP 工具，复用原 Registry 和查询函数。
- `app/mcp/client.py`：发现工具、打印 Schema、发起一次调用并断言返回的商品 ID。
- `tests/mcp/test_client_launch.py`：验证模块启动入口、工作目录和传入的环境配置，不启动子进程。
- `requirements-mcp.txt`：可选依赖，使用官方 SDK 2.x；不修改基础 CI 的安装范围。
- `tests/mcp/test_mcp_catalog.py`：同进程的 MCP 参数校验、权限拒绝与工具超时测试。

## 学习者执行

在项目目录和 `learning_zero` 环境中执行：

```powershell
python -m pip install -r requirements-mcp.txt
python -m pip check
```

如当前窗口没有应用密钥，只为本机演示生成一个临时值；已有值保持不变：

```powershell
if (-not $env:APP_SECRET_KEY) {
    $env:APP_SECRET_KEY = [guid]::NewGuid().ToString("N")
}
python -m app.mcp.client --item-id 101
```

客户端使用当前 Python 解释器执行 `-u -m app.mcp.server`，子进程工作目录固定为项目根目录。
移动源码不会移动数据库，仍访问 `data/app/dev.db`；通信方式仍为 stdio，不是 HTTP。

预期发现 `get_item`，Schema 要求整数 `item_id`，返回结果中的商品 ID 为 101，最后打印 PASS。
实际商品名称和价格来自应用库，不是 SQL 实验库。若商品 101 已被删除，应选择应用库中真实存在的 ID，不能把不存在的商品也断言为成功。

## 边界与当前状态

- 服务端固定持有 `catalog:read`，工具参数不能声明权限；本轮没有 JWT 用户认证或角色映射。
- 复用的 `main.py` 需要 `APP_SECRET_KEY`。这是原应用加载要求，不是 MCP 客户端认证。
- 服务端加载原应用时会检查表结构，空库会写入原有种子数据；工具查询函数本身不写商品。
- 标准输出用于协议通信；服务端日志应写标准错误，客户端才负责打印学习用结果。
- MCP 工具失败用 `is_error` 表示；成功时 `structured_content` 保存原 Registry 结果和 trace。
- Registry 的超时限制等待时间，不保证强制停止正在执行的线程。
- 学习者已安装 `mcp==2.1.1`，`pip check` 无冲突；stdio 冒烟已发现 `get_item` 并查询商品101成功。
- 模型 Planner 尚未接入 MCP，HTTP 跨设备调用也尚未配置。

## 三类失败测试（待学习者运行）

```powershell
python -m pytest tests/mcp/test_mcp_catalog.py -v -s
```

| 测试 | 如何制造条件 | 关键断言 |
| --- | --- | --- |
| 无权限 | 测试临时把服务端权限替换为空集合 | `is_error=True`、包含 `PERMISSION_DENIED`、假查询未执行 |
| 坏参数 | 发送字符串 `"101"` 而非整数 `101` | SDK 拒绝参数、假查询未执行 |
| 超时 | 假查询等待事件，注册表只等待20毫秒 | `TIMEOUT`、带 trace、没有成功数据 |

三条用例通过 `Client(server.mcp)` 在同一进程中调用，保留 SDK 校验及原注册表逻辑，
没有走 stdio 子进程或 HTTP。超时测的是注册表等待业务处理器的上限，不是网络超时或模型超时。
假处理器不查询数据库；但根目录 `conftest.py` 仍会按原有 pytest 配置初始化、重置隔离测试库，不能说整个 pytest 完全不碰数据库。
无权限条件由测试注入，不代表已实现真实用户身份认证。测试结束后 monkeypatch 恢复原配置。

## 本地模型为什么也使用 HTTP

模型权重是保存在磁盘上的数据文件；Ollama 是加载模型并提供推理接口的本地程序。
当前 Python 程序把 JSON 请求发送到 `http://127.0.0.1:11434/api/chat`，Ollama 调用本机模型后返回 JSON。
`127.0.0.1` 指本机回环地址，`11434` 是 Ollama 本地服务端口；HTTP 不等于公网网站。
关闭本机 Ollama 服务后，模型文件仍在，但这个 HTTP 调用入口将不可用。
这条模型推理连接与 MCP stdio 工具调用连接是两个独立环节。

参考：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[stdio 客户端](https://py.sdk.modelcontextprotocol.io/client/transports/)。
