"""测试 MCP 的三类失败处理；使用内存连接与假查询，不启动真实模型。"""

import asyncio
import threading
from unittest.mock import Mock

from mcp import Client
from mcp.types import TextContent
import pytest

from app.agent.mvp import GET_ITEM_SCHEMA, ToolRegistry, ToolSpec
import mcp_catalog_server as server


@pytest.fixture
def install_test_registry(monkeypatch):
    """替换查询处理器及进程权限，但保留真实的注册表校验和超时逻辑。"""
    def install(handler, permissions=frozenset({"catalog:read"}), timeout_seconds=1.0):
        registry = ToolRegistry([
            ToolSpec(
                name="get_item",
                description="测试商品详情查询",
                input_schema=GET_ITEM_SCHEMA,
                required_permission="catalog:read",
                handler=handler,
                timeout_seconds=timeout_seconds,
            )
        ])
        monkeypatch.setattr(server, "registry", registry)
        monkeypatch.setattr(server, "SERVER_PERMISSIONS", permissions)
    return install


async def call_get_item(arguments):
    """通过 SDK 内存客户端调用注册工具，仍经过 MCP 参数校验和错误转换。"""
    async with asyncio.timeout(10), Client(server.mcp) as client:
        return await client.call_tool("get_item", arguments)


def error_text(response):
    """从 MCP 返回的文本内容块中取出错误信息供断言使用。"""
    return "\n".join(block.text for block in response.content if isinstance(block, TextContent))


def test_permission_denied_does_not_query(install_test_registry):
    """移除服务端权限后应返回权限错误，并且不能执行商品查询函数。"""
    query = Mock(return_value={"status": "success", "data": {"id": 101}})
    install_test_registry(query, permissions=frozenset())

    response = asyncio.run(call_get_item({"item_id": 101}))

    assert response.is_error is True
    assert "PERMISSION_DENIED" in error_text(response)
    assert "trace_id=" in error_text(response)
    assert response.structured_content is None
    query.assert_not_called()


def test_string_item_id_is_rejected_before_query(install_test_registry):
    """传入字符串而不是整数，验证 StrictInt 参数校验在查询前拒绝请求。"""
    query = Mock(return_value={"status": "success", "data": {"id": 101}})
    install_test_registry(query)

    response = asyncio.run(call_get_item({"item_id": "101"}))

    assert response.is_error is True
    assert "item_id" in error_text(response)
    assert response.structured_content is None
    query.assert_not_called()


def test_slow_query_returns_timeout_without_success_data(install_test_registry):
    """让假查询等待事件，验证真实超时逻辑会返回错误而非迟到的成功数据。"""
    release_query = threading.Event()

    def slow_query(arguments):
        # 正常在测试结束时放行；两秒上限用于防止测试异常时留下无限等待线程。
        release_query.wait(timeout=2.0)
        return {"status": "success", "data": {"id": arguments["item_id"]}}

    install_test_registry(slow_query, timeout_seconds=0.02)
    try:
        response = asyncio.run(call_get_item({"item_id": 101}))
        assert response.is_error is True
        assert "TIMEOUT" in error_text(response)
        assert "trace_id=" in error_text(response)
        assert response.structured_content is None
    finally:
        # 注册表超时不等于杀死线程；测试负责解除假处理器的等待。
        release_query.set()
