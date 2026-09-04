"""把现有商品详情工具包装成通过标准输入输出通信的 MCP 服务。"""

import logging
import uuid
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import StrictInt

from app.agent.mvp import ToolCall, build_catalog_registry


mcp = MCPServer("sdet-catalog")
registry = build_catalog_registry()
logger = logging.getLogger(__name__)

# 这是本地演示进程的固定权限，不代表已经认证了某个用户。
# 权限不放进工具参数，调用方不能通过参数给自己授权。
SERVER_PERMISSIONS = frozenset({"catalog:read"})


@mcp.tool()
def get_item(item_id: StrictInt) -> dict[str, Any]:
    """按整数商品 ID 查询商品详情，只提供查询功能。"""
    trace_id = uuid.uuid4().hex
    result = registry.execute(
        ToolCall("get_item", {"item_id": item_id}, trace_id, SERVER_PERMISSIONS)
    )
    # 日志走标准错误；标准输出留给 MCP 协议消息。
    logger.info("trace_id=%s tool=get_item ok=%s", trace_id, result.ok)
    if not result.ok:
        # 工具失败应标记为 MCP 错误，避免调用方把错误数据当作成功结果。
        raise ToolError(f"{result.error_type}; trace_id={trace_id}")
    return result.to_dict()


if __name__ == "__main__":
    # 提前加载原应用数据库，避免首次导入的耗时混入工具的一秒超时窗口。
    # 原 main.py 会检查表结构，并在空库中写入原有种子数据。
    import main

    mcp.run(transport="stdio")
