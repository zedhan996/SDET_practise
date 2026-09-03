import os

import pytest

from agent_mvp import (
    GET_ITEM_SCHEMA,
    SEARCH_ITEMS_SCHEMA,
    ToolCallingAgent,
    ToolRegistry,
    ToolSpec,
)
from agent_ollama import OllamaToolPlanner
from agent_rag import SEARCH_KNOWLEDGE_SCHEMA


ALL_READ = frozenset({"catalog:read", "knowledge:read"})


# 真实Tool Calling需要正在运行的Ollama，普通测试和CI默认跳过。
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_OLLAMA_INTEGRATION") != "1",
        reason="设置 RUN_OLLAMA_INTEGRATION=1 后才调用真实Qwen Planner",
    ),
]


def build_integration_registry() -> ToolRegistry:
    """保留真实工具契约，使用无副作用处理器观察Qwen的选择和参数。"""
    return ToolRegistry(
        [
            ToolSpec(
                "search_items",
                "按商品名称关键词和最高价格搜索商品。",
                SEARCH_ITEMS_SCHEMA,
                "catalog:read",
                lambda arguments: arguments,
            ),
            ToolSpec(
                "get_item",
                "根据数字商品ID查询一个商品的详细信息。",
                GET_ITEM_SCHEMA,
                "catalog:read",
                lambda arguments: arguments,
            ),
            ToolSpec(
                "search_knowledge",
                "查询鉴权、状态码、日志、request_id和商品规则知识库。",
                SEARCH_KNOWLEDGE_SCHEMA,
                "knowledge:read",
                lambda arguments: arguments,
            ),
        ]
    )


def build_real_agent() -> ToolCallingAgent:
    """使用本机qwen3:4b-instruct规划，但不触碰真实数据库或向量库。"""
    registry = build_integration_registry()
    planner = OllamaToolPlanner.from_registry(
        registry,
        model="qwen3:4b-instruct",
        timeout_seconds=60,
    )
    return ToolCallingAgent(planner=planner, registry=registry)


# 验证真实Qwen能把自然语言商品详情问题转换为get_item及整数参数。
def test_real_qwen_planner_selects_get_item_with_id():
    result = build_real_agent().run(
        "请帮我查询商品ID 101的详细信息。",
        ALL_READ,
        trace_id="qwen-planner-item-001",
    )

    assert result.ok is True
    assert result.trace.tool_name == "get_item"
    assert result.data == {"item_id": 101}
    assert result.trace.trace_id == "qwen-planner-item-001"


# 验证真实Qwen能区分业务数据查询和规则知识问题并选择search_knowledge。
def test_real_qwen_planner_selects_knowledge_search():
    result = build_real_agent().run(
        "没有携带登录令牌时，接口应该返回什么状态码？",
        ALL_READ,
        trace_id="qwen-planner-knowledge-001",
    )

    assert result.ok is True
    assert result.trace.tool_name == "search_knowledge"
    assert isinstance(result.data["query"], str)
    assert result.data["query"].strip()
    assert result.trace.trace_id == "qwen-planner-knowledge-001"
