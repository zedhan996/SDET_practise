import time

from app.agent.mvp import (
    EvaluationCase,
    OfflineQueryAgent,
    OfflineQueryPlanner,
    ToolCall,
    ToolCallingAgent,
    ToolRegistry,
    ToolSpec,
    build_catalog_registry,
    evaluate_case,
    evaluate_cases,
    parse_tool_call,
)


# 模拟一个拥有商品只读权限的调用者；没有这个权限时预期拒绝调用。
READ_PERMISSION = frozenset({"catalog:read"})


# 单独检查注册表暴露的两个工具及其参数、权限契约是否稳定。
def test_catalog_tools_expose_stable_contracts():
    tools = build_catalog_registry().list_tools()

    assert [tool["name"] for tool in tools] == ["search_items", "get_item"]
    assert tools[0]["input_schema"]["additionalProperties"] is False
    assert tools[1]["input_schema"]["required"] == ["item_id"]
    assert all(tool["required_permission"] == "catalog:read" for tool in tools)


# 联动 ToolCall、ToolRegistry 和真实数据库，验证搜索结果及 trace 记录。
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


# 单独验证 get_item 工具能按商品 ID 查询正确的数据库记录。
def test_get_item_tool_returns_item_by_id():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 102}, "agent-get-001", READ_PERMISSION)
    )

    assert result.ok is True
    assert result.data["data"]["name"] == "MacBook Pro"


# 验证参数 Schema 先于 handler 执行，错误类型统一为 INVALID_ARGUMENT。
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


# 验证没有 catalog:read 权限时，在执行业务工具前返回权限错误。
def test_permission_is_required_before_tool_execution():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 101}, "agent-permission-001", frozenset())
    )

    assert result.ok is False
    assert result.error_type == "PERMISSION_DENIED"
    assert result.data is None


# 验证白名单会拦截未注册工具，防止 Agent 调用任意业务函数。
def test_unknown_tool_is_rejected_by_the_whitelist():
    result = build_catalog_registry().execute(
        ToolCall("delete_item", {"item_id": 101}, "agent-unknown-001", READ_PERMISSION)
    )

    assert result.ok is False
    assert result.error_type == "TOOL_NOT_FOUND"


# 联动真实数据库验证商品不存在时返回结构化的 ITEM_NOT_FOUND 错误。
def test_missing_item_returns_structured_business_error():
    result = build_catalog_registry().execute(
        ToolCall("get_item", {"item_id": 999999}, "agent-missing-001", READ_PERMISSION)
    )

    assert result.ok is False
    assert result.error_type == "ITEM_NOT_FOUND"
    assert result.message == "Item 999999 not found"


# 注册一个慢工具，验证 ToolRegistry 超时返回且不等待完整执行时间。
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


# 联动 OfflineQueryAgent、Planner 和 Registry，验证商品 ID 查询能选对工具。
def test_offline_agent_maps_item_id_query_to_get_tool():
    result = OfflineQueryAgent().run(
        "请查询商品 ID 101", READ_PERMISSION, trace_id="agent-natural-001"
    )

    assert result.ok is True
    assert result.data["data"]["id"] == 101
    assert result.trace.tool_name == "get_item"


# 单独验证 Planner 只生成 ToolCall，不直接执行数据库或业务工具。
def test_planner_only_creates_a_tool_call_without_executing_it():
    call = OfflineQueryPlanner().plan(
        "请查询商品 ID 101",
        READ_PERMISSION,
        trace_id="planner-001",
    )

    assert isinstance(call, ToolCall)
    assert call.tool_name == "get_item"
    assert call.arguments == {"item_id": 101}
    assert call.trace_id == "planner-001"


# 单独验证模型风格字典可以被解析为内部 ToolCall 对象。
def test_parser_converts_model_style_dict_to_tool_call():
    call = parse_tool_call(
        {"tool_name": "get_item", "arguments": {"item_id": 101}},
        READ_PERMISSION,
        trace_id="parser-001",
    )

    assert isinstance(call, ToolCall)
    assert call.tool_name == "get_item"
    assert call.arguments == {"item_id": 101}
    assert call.permissions == READ_PERMISSION
    assert call.trace_id == "parser-001"


# 验证模型不能通过输出 permissions 字段伪造调用者权限。
def test_parser_does_not_accept_model_supplied_permissions():
    try:
        parse_tool_call(
            {
                "tool_name": "get_item",
                "arguments": {"item_id": 101},
                "permissions": ["catalog:read"],
            },
            frozenset(),
            trace_id="parser-002",
        )
    except Exception as exc:
        assert getattr(exc, "error_type", None) == "INVALID_ARGUMENT"
    else:
        raise AssertionError("model-supplied permissions must be rejected")


# 批量检查缺失字段、错误类型和错误参数结构都会被解析器拒绝。
def test_parser_rejects_missing_or_wrongly_typed_fields():
    invalid_calls = [
        {"arguments": {"item_id": 101}},
        {"tool_name": "get_item"},
        {"tool_name": 123, "arguments": {}},
        {"tool_name": "get_item", "arguments": []},
    ]

    for raw_call in invalid_calls:
        try:
            parse_tool_call(raw_call, READ_PERMISSION, trace_id="parser-invalid")
        except Exception as exc:
            assert getattr(exc, "error_type", None) == "INVALID_ARGUMENT"
        else:
            raise AssertionError(f"invalid call was accepted: {raw_call}")


# 使用返回字典的模型适配器，验证 ToolCallingAgent 能接入可替换 Planner。
def test_tool_calling_agent_can_parse_a_dict_from_a_model_adapter():
    class ModelStylePlanner:
        def plan(self, _user_text, _permissions, _trace_id):
            return {"tool_name": "get_item", "arguments": {"item_id": 102}}

    result = ToolCallingAgent(planner=ModelStylePlanner()).run(
        "查询商品",
        READ_PERMISSION,
        trace_id="model-adapter-001",
    )

    assert result.ok is True
    assert result.data["data"]["id"] == 102
    assert result.trace.trace_id == "model-adapter-001"


# 评测一条正常搜索链路，分别断言工具、参数、结果和 trace。
def test_evaluation_case_checks_tool_arguments_result_and_trace():
    case = EvaluationCase(
        case_id="eval-search-001",
        user_text="帮我查找 6000 元以内的 iPhone",
        permissions=READ_PERMISSION,
        expected_tool="search_items",
        expected_arguments={"keyword": "iPhone", "max_price": 6000.0},
    )

    evaluation = evaluate_case(case)

    assert evaluation.passed is True
    assert all(evaluation.checks.values())
    assert evaluation.result.trace.trace_id == "eval-eval-search-001"


# 评测无权限链路，验证工具和参数识别正确但执行被拒绝。
def test_evaluation_case_records_expected_permission_failure():
    case = EvaluationCase(
        case_id="eval-permission-001",
        user_text="请查询商品 ID 101",
        permissions=frozenset(),
        expected_tool="get_item",
        expected_arguments={"item_id": 101},
        expected_ok=False,
        expected_error_type="PERMISSION_DENIED",
    )

    evaluation = evaluate_case(case)

    assert evaluation.passed is True
    assert evaluation.result.error_type == "PERMISSION_DENIED"


# 批量执行多个版本化用例，验证 Harness 能汇总每条用例的通过状态。
def test_evaluate_cases_runs_a_versioned_batch():
    cases = [
        EvaluationCase(
            case_id="batch-get-001",
            user_text="查询商品 ID 101",
            permissions=READ_PERMISSION,
            expected_tool="get_item",
            expected_arguments={"item_id": 101},
        ),
        EvaluationCase(
            case_id="batch-search-001",
            user_text="搜索 iPhone",
            permissions=READ_PERMISSION,
            expected_tool="search_items",
            expected_arguments={"keyword": "iPhone", "max_price": None},
        ),
    ]

    evaluations = evaluate_cases(cases)

    assert len(evaluations) == 2
    assert all(evaluation.passed for evaluation in evaluations)


# 联动 OfflineQueryAgent 和真实查询工具，验证价格条件搜索能选对工具。
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


# 用固定 Stub Planner 替代模型，单独验证执行层与 Planner 的解耦关系。
def test_tool_calling_agent_accepts_a_replaceable_planner():
    class StubPlanner:
        def plan(self, _user_text, permissions, trace_id):
            return ToolCall("get_item", {"item_id": 103}, trace_id, permissions)

    result = ToolCallingAgent(planner=StubPlanner()).run(
        "任意输入",
        READ_PERMISSION,
        trace_id="stub-planner-001",
    )

    assert result.ok is True
    assert result.data["data"]["id"] == 103
    assert result.trace.trace_id == "stub-planner-001"


# 验证 Planner 输入失败时也转换成统一的 INVALID_ARGUMENT 结果。
def test_planner_failure_is_returned_as_a_structured_error():
    result = ToolCallingAgent().run("", READ_PERMISSION, trace_id="planner-error-001")

    assert result.ok is False
    assert result.error_type == "INVALID_ARGUMENT"
    assert result.trace.tool_name == "planner"
