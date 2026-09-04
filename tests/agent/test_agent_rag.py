from app.agent.mvp import ToolCall, ToolCallingAgent
from app.agent.rag import (
    OfflineCatalogKnowledgePlanner,
    RagKnowledgeToolHandler,
    build_catalog_knowledge_registry,
)
from app.rag.mvp import RetrievalResult, RetrievedChunk
from app.rag.reranker import CandidateReranker


CATALOG_READ = frozenset({"catalog:read"})
KNOWLEDGE_READ = frozenset({"knowledge:read"})


class StubStore:
    """按调用时的query和trace_id返回预设候选，避免加载真实模型。"""

    def __init__(self, hits: list[RetrievedChunk]):
        self.hits = hits

    def retrieve(self, query, top_k, trace_id=None):
        return RetrievalResult(
            query=query,
            top_k=top_k,
            trace_id=trace_id or "stub-rag-trace",
            hits=self.hits,
            answerable=bool(self.hits),
            rejection_reason=None if self.hits else "EMPTY_KNOWLEDGE_BASE",
        )


def make_hit(source: str, content: str, similarity: float) -> RetrievedChunk:
    """构造一条带来源和相似度的RAG候选。"""
    return RetrievedChunk(
        chunk_id=source,
        content=content,
        source=source,
        version="v1",
        distance=1 - similarity,
        similarity=similarity,
    )


def make_handler(
    hits: list[RetrievedChunk] | None = None,
    scores: list[float] | None = None,
) -> RagKnowledgeToolHandler:
    """构造完全离线的知识工具处理器。"""
    actual_hits = hits if hits is not None else [
        make_hit("knowledge/auth-rule.md", "没有有效令牌时返回401。", 0.36)
    ]
    actual_scores = scores if scores is not None else [0.08] * len(actual_hits)
    return RagKnowledgeToolHandler(
        store=StubStore(actual_hits),
        reranker=CandidateReranker(lambda _pairs: actual_scores),
    )


# 注册表应同时暴露两个商品工具和一个受knowledge:read保护的知识工具。
def test_registry_exposes_catalog_and_knowledge_tools():
    registry = build_catalog_knowledge_registry(make_handler())
    tools = {tool["name"]: tool for tool in registry.list_tools()}

    assert set(tools) == {"search_items", "get_item", "search_knowledge"}
    assert tools["search_knowledge"]["required_permission"] == "knowledge:read"
    assert tools["search_knowledge"]["input_schema"]["required"] == ["query"]


# 知识问题应选择search_knowledge，并让Agent与RAG内部沿用同一个trace_id。
def test_agent_routes_knowledge_question_and_preserves_trace():
    agent = ToolCallingAgent(
        planner=OfflineCatalogKnowledgePlanner(),
        registry=build_catalog_knowledge_registry(make_handler()),
    )

    result = agent.run(
        "没有登录令牌时接口应该返回什么状态码？",
        KNOWLEDGE_READ,
        trace_id="agent-rag-001",
    )

    assert result.ok is True
    assert result.trace.tool_name == "search_knowledge"
    assert result.trace.trace_id == "agent-rag-001"
    assert result.data["answerable"] is True
    assert result.data["source"] == "knowledge/auth-rule.md"
    assert result.data["trace_id"] == "agent-rag-001"


# 缺少knowledge:read权限时必须在RAG检索之前拒绝调用。
def test_knowledge_tool_requires_permission_before_retrieval():
    agent = ToolCallingAgent(
        planner=OfflineCatalogKnowledgePlanner(),
        registry=build_catalog_knowledge_registry(make_handler()),
    )

    result = agent.run(
        "鉴权规则是什么？",
        CATALOG_READ,
        trace_id="agent-rag-permission-001",
    )

    assert result.ok is False
    assert result.error_type == "PERMISSION_DENIED"
    assert result.trace.tool_name == "search_knowledge"


# Reranker门禁拒答属于工具正常完成，不能伪装成系统异常或泄露低相关内容。
def test_knowledge_tool_returns_safe_rejection_without_candidate_content():
    handler = make_handler(
        hits=[make_hit("knowledge/catalog-rule.md", "商品价格规则", 0.45)],
        scores=[0.003],
    )
    registry = build_catalog_knowledge_registry(handler)

    result = registry.execute(
        ToolCall(
            "search_knowledge",
            {"query": "商品价格由哪位管理员审批？"},
            "agent-rag-rejected-001",
            KNOWLEDGE_READ,
        )
    )

    assert result.ok is True
    assert result.data["status"] == "rejected"
    assert result.data["answerable"] is False
    assert result.data["content"] is None
    assert result.data["source"] is None
    assert result.data["rejection_reason"] == "LOW_RERANKER_SCORE"


# 空白知识问题虽然类型正确，仍应由处理器作为非法业务参数拒绝。
def test_knowledge_tool_rejects_blank_query():
    registry = build_catalog_knowledge_registry(make_handler())

    result = registry.execute(
        ToolCall(
            "search_knowledge",
            {"query": "   "},
            "agent-rag-blank-001",
            KNOWLEDGE_READ,
        )
    )

    assert result.ok is False
    assert result.error_type == "INVALID_ARGUMENT"
    assert result.message == "query must not be blank"


# 非知识类商品详情问题继续交给原有get_item工具，验证新增分流没有破坏旧能力。
def test_agent_keeps_catalog_query_on_existing_tool():
    agent = ToolCallingAgent(
        planner=OfflineCatalogKnowledgePlanner(),
        registry=build_catalog_knowledge_registry(make_handler()),
    )

    result = agent.run(
        "请查询商品 ID 101",
        CATALOG_READ,
        trace_id="agent-catalog-after-rag-001",
    )

    assert result.ok is True
    assert result.trace.tool_name == "get_item"
    assert result.data["data"]["id"] == 101
