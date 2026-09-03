import json
import socket
import pytest
from typing import Any
from urllib import error as url_error

from agent_mvp import ToolCallingAgent, ToolRegistry, ToolSpec
from agent_ollama import OllamaToolPlanner, OLLAMA_PLANNER_SYSTEM_PROMPT, get_planner_prompt


CATALOG_READ = frozenset({"catalog:read"})
KNOWLEDGE_READ = frozenset({"knowledge:read"})
ALL_READ = CATALOG_READ | KNOWLEDGE_READ


def build_test_registry() -> ToolRegistry:
    """使用轻量假处理器复现三个真实工具的名称、Schema和权限边界。"""
    return ToolRegistry(
        [
            ToolSpec(
                name="search_items",
                description="按名称和最高价格搜索商品。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {"type": ["string", "null"]},
                        "max_price": {"type": ["number", "null"]},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                required_permission="catalog:read",
                handler=lambda arguments: {"selected": "search_items", **arguments},
            ),
            ToolSpec(
                name="get_item",
                description="根据数字ID查询单个商品。",
                input_schema={
                    "type": "object",
                    "properties": {"item_id": {"type": "integer"}},
                    "required": ["item_id"],
                    "additionalProperties": False,
                },
                required_permission="catalog:read",
                handler=lambda arguments: {"selected": "get_item", **arguments},
            ),
            ToolSpec(
                name="search_knowledge",
                description="查询鉴权、日志和商品规则知识库。",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                required_permission="knowledge:read",
                handler=lambda arguments: {
                    "selected": "search_knowledge",
                    **arguments,
                },
            ),
        ]
    )


def make_tool_response(tool_name: str, arguments: Any) -> bytes:
    """构造Ollama原生tool_calls响应，避免单元测试访问真实模型。"""
    return json.dumps(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    }
                ],
            },
            "done": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def make_planner(
    response: bytes,
    captured: dict[str, Any] | None = None,
) -> OllamaToolPlanner:
    """注入固定HTTP响应，并按需记录Planner发出的请求。"""
    registry = build_test_registry()

    def transport(http_request, timeout_seconds):
        if captured is not None:
            captured["request"] = http_request
            captured["timeout_seconds"] = timeout_seconds
        return response

    return OllamaToolPlanner.from_registry(registry, transport=transport)


# 验证内部工具契约能转成Ollama格式，且权限和trace不会发送给模型。
def test_planner_builds_native_tool_call_request_without_security_fields():
    captured: dict[str, Any] = {}
    planner = make_planner(
        make_tool_response("get_item", {"item_id": 101}),
        captured,
    )

    planned = planner.plan(
        "请查询商品101",
        ALL_READ,
        trace_id="ollama-planner-request-001",
    )
    payload = json.loads(captured["request"].data.decode("utf-8"))

    assert planned == {"tool_name": "get_item", "arguments": {"item_id": 101}}
    assert captured["request"].full_url == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout_seconds"] == 60.0
    assert payload["stream"] is False
    assert payload["think"] is False
    assert {tool["function"]["name"] for tool in payload["tools"]} == {
        "search_items",
        "get_item",
        "search_knowledge",
    }
    assert all("required_permission" not in tool for tool in payload["tools"])
    assert "ollama-planner-request-001" not in json.dumps(payload)
    assert "knowledge:read" not in json.dumps(payload)


# 验证真实模型风格的知识工具计划能进入Registry并保留应用生成的trace_id。
def test_agent_executes_model_selected_knowledge_tool_and_preserves_trace():
    registry = build_test_registry()
    planner = make_planner(
        make_tool_response(
            "search_knowledge",
            {"query": "没有登录令牌时返回什么？"},
        )
    )
    agent = ToolCallingAgent(planner=planner, registry=registry)

    result = agent.run(
        "没有登录令牌时返回什么？",
        KNOWLEDGE_READ,
        trace_id="ollama-agent-knowledge-001",
    )

    assert result.ok is True
    assert result.data["selected"] == "search_knowledge"
    assert result.trace.tool_name == "search_knowledge"
    assert result.trace.trace_id == "ollama-agent-knowledge-001"


# 即使模型选择了知识工具，调用方未授予knowledge:read时仍由Registry拒绝。
def test_model_selection_cannot_bypass_registry_permission_check():
    planner = make_planner(
        make_tool_response("search_knowledge", {"query": "鉴权规则"})
    )
    agent = ToolCallingAgent(planner=planner, registry=build_test_registry())

    result = agent.run("鉴权规则是什么？", CATALOG_READ)

    assert result.ok is False
    assert result.error_type == "PERMISSION_DENIED"
    assert result.trace.tool_name == "search_knowledge"


# 模型编造白名单外工具时，Planner只转交计划，Registry必须最终拒绝执行。
def test_model_invented_tool_is_rejected_by_registry_allowlist():
    planner = make_planner(make_tool_response("delete_all_items", {}))
    agent = ToolCallingAgent(planner=planner, registry=build_test_registry())

    result = agent.run("删除全部商品", ALL_READ)

    assert result.ok is False
    assert result.error_type == "TOOL_NOT_FOUND"
    assert result.trace.tool_name == "delete_all_items"


# 模型只输出自然语言而没有tool_calls时，应返回稳定的规划阶段错误。
def test_agent_reports_when_model_does_not_choose_a_tool():
    response = json.dumps(
        {"message": {"role": "assistant", "content": "我直接回答。"}}
    ).encode("utf-8")
    agent = ToolCallingAgent(
        planner=make_planner(response),
        registry=build_test_registry(),
    )

    result = agent.run("查询商品", ALL_READ, trace_id="no-tool-call-001")

    assert result.ok is False
    assert result.error_type == "PLANNER_NO_TOOL_CALL"
    assert result.trace.tool_name == "planner"
    assert result.trace.trace_id == "no-tool-call-001"


# 当前MVP每轮只允许一个工具调用，模型并行提出多个调用时应明确失败。
def test_agent_rejects_multiple_model_tool_calls():
    tool_call = json.loads(make_tool_response("get_item", {"item_id": 101}))[
        "message"
    ]["tool_calls"][0]
    response = json.dumps(
        {
            "message": {
                "tool_calls": [
                    tool_call,
                    {
                        "function": {
                            "name": "get_item",
                            "arguments": {"item_id": 102},
                        }
                    },
                ]
            }
        }
    ).encode("utf-8")
    agent = ToolCallingAgent(
        planner=make_planner(response),
        registry=build_test_registry(),
    )

    result = agent.run("查询商品101和102", ALL_READ)

    assert result.ok is False
    assert result.error_type == "PLANNER_MULTIPLE_TOOL_CALLS"


# arguments必须是JSON对象，字符串形式不能绕过后续Schema校验。
def test_agent_rejects_non_object_model_arguments():
    planner = make_planner(make_tool_response("get_item", '{"item_id": 101}'))
    agent = ToolCallingAgent(planner=planner, registry=build_test_registry())

    result = agent.run("查询商品101", ALL_READ)

    assert result.ok is False
    assert result.error_type == "PLANNER_INVALID_RESPONSE"


# 模拟Ollama响应超时，验证网络故障能转换成稳定的PLANNER_TIMEOUT。
def test_agent_maps_ollama_timeout_to_planner_error():
    registry = build_test_registry()

    def timeout_transport(_http_request, _timeout_seconds):
        raise socket.timeout("simulated timeout")

    planner = OllamaToolPlanner.from_registry(
        registry,
        transport=timeout_transport,
    )
    result = ToolCallingAgent(planner=planner, registry=registry).run(
        "查询商品101",
        ALL_READ,
    )

    assert result.ok is False
    assert result.error_type == "PLANNER_TIMEOUT"


# 模拟Ollama服务未启动，验证连接失败不会泄露成未处理异常。
def test_agent_maps_ollama_unavailable_to_planner_error():
    registry = build_test_registry()

    def unavailable_transport(_http_request, _timeout_seconds):
        raise url_error.URLError(ConnectionRefusedError("simulated refusal"))

    planner = OllamaToolPlanner.from_registry(
        registry,
        transport=unavailable_transport,
    )
    result = ToolCallingAgent(planner=planner, registry=registry).run(
        "查询商品101",
        ALL_READ,
    )

    assert result.ok is False
    assert result.error_type == "PLANNER_UNAVAILABLE"


@pytest.mark.parametrize("version", ["v0", "v1"])
def test_selected_prompt_is_sent_without_changing_other_request_fields(version):
    """截获请求验证版本真正进入system消息，而工具契约和用户输入不变。"""
    captured = {}

    def transport(request, _timeout):
        captured["payload"] = json.loads(request.data)
        return make_tool_response("get_item", {"item_id": 101})

    planner = OllamaToolPlanner.from_registry(
        build_test_registry(), prompt_version=version, transport=transport,
    )
    planner.plan("查询商品101", CATALOG_READ)
    payload = captured["payload"]
    assert payload["messages"][0]["content"] == get_planner_prompt(version)
    assert payload["messages"][1]["content"] == "查询商品101"
    assert payload["model"] == "qwen3:4b-instruct"
    assert payload["options"] == {"temperature": 0.0, "num_predict": 256}
    assert len(payload["tools"]) == 3
    assert get_planner_prompt("v0") == OLLAMA_PLANNER_SYSTEM_PROMPT
    assert get_planner_prompt("v1") != get_planner_prompt("v0")


def test_unknown_prompt_version_is_rejected():
    """版本拼错时应在发送请求前失败，不悄悄使用默认提示词。"""
    with pytest.raises(ValueError, match="prompt_version"):
        OllamaToolPlanner.from_registry(build_test_registry(), prompt_version="typo")
