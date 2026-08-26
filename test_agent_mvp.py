import time

from agent_mvp import (
    OfflineQueryAgent,
    ToolCall,
    ToolRegistry,
    ToolSpec,
    build_catalog_registry,
)


# 模拟一个拥有商品只读权限的调用者；没有这个权限时预期拒绝调用。
READ_PERMISSION = frozenset({"catalog:read"})


def test_catalog_tools_expose_stable_contracts():
    tools = build_catalog_registry().list_tools()

    assert [tool["name"] for tool in tools] == ["search_items", "get_item"]
    assert tools[0]["input_schema"]["additionalProperties"] is False
    assert tools[1]["input_schema"]["required"] == ["item_id"]
    assert all(tool["required_permission"] == "catalog:read" for tool in tools)


def test_search_tool_returns_real_catalog_data_and_trace():
    result = build_catalog_registry().execute(
        ToolCall(
            tool_name="search_items",
            arguments={"keyword": "iPhone", "max_price": None},
            trace_id="agent-search-001",
            permissions=READ_PERMISSION,
        )
    )

    assert result.ok is True
    assert result.data["total"] == 1
    assert result.data["data"][0]["id"] == 101
    assert result.trace.trace_id == "agent-search-001"
    assert result.trace.status == "success"
    assert result.trace.duration_ms >= 0


def test_get_item_tool_returns_item_by_id():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 102}, "agent-get-001", READ_PERMISSION)
    )

    assert result.ok is True
    assert result.data["data"]["name"] == "MacBook Pro"


def test_invalid_arguments_are_rejected_before_handler_execution():
    result = build_catalog_registry().execute(
        ToolCall(
            "search_items",
            {"keyword": 123, "max_price": -1},
            "agent-invalid-001",
            READ_PERMISSION,
        )
    )

    assert result.ok is False
    assert result.error_type == "INVALID_ARGUMENT"
    assert result.trace.error_type == "INVALID_ARGUMENT"


def test_permission_is_required_before_tool_execution():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 101}, "agent-permission-001", frozenset())
    )

    assert result.ok is False
    assert result.error_type == "PERMISSION_DENIED"
    assert result.data is None


def test_unknown_tool_is_rejected_by_the_whitelist():
    result = build_catalog_registry().execute(
        ToolCall("delete_item", {"item_id": 101}, "agent-unknown-001", READ_PERMISSION)
    )

    assert result.ok is False
    assert result.error_type == "TOOL_NOT_FOUND"


def test_missing_item_returns_structured_business_error():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 999999}, "agent-missing-001", READ_PERMISSION)
    )

    assert result.ok is False
    assert result.error_type == "ITEM_NOT_FOUND"
    assert result.message == "Item 999999 not found"


def test_timeout_returns_without_waiting_for_the_full_handler_duration():
    def slow_tool(_arguments):
        time.sleep(0.1)
        return {"status": "success"}

    registry = ToolRegistry(
        [
            ToolSpec(
                name="slow_tool",
                description="Test-only slow tool.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                required_permission="catalog:read",
                handler=slow_tool,
                timeout_seconds=0.01,
            )
        ]
    )

    started_at = time.perf_counter()
    result = registry.execute(ToolCall("slow_tool", {}, "agent-timeout-001", READ_PERMISSION))
    elapsed = time.perf_counter() - started_at

    assert result.ok is False
    assert result.error_type == "TIMEOUT"
    assert elapsed < 0.08


def test_offline_agent_maps_item_id_query_to_get_tool():
    result = OfflineQueryAgent().run(
        "请查询商品 ID 101", READ_PERMISSION, trace_id="agent-natural-001"
    )

    assert result.ok is True
    assert result.data["data"]["id"] == 101
    assert result.trace.tool_name == "get_item"


def test_offline_agent_maps_search_query_to_search_tool():
    result = OfflineQueryAgent().run(
        "帮我查找 6000 元以内的 iPhone",
        READ_PERMISSION,
        trace_id="agent-natural-002",
    )

    assert result.ok is True
    assert result.data["total"] == 1
    assert result.data["data"][0]["id"] == 101
    assert result.trace.tool_name == "search_items"
