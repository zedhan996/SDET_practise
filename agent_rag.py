"""把受控RAG检索注册为Agent工具，并提供离线分流Planner。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent_mvp import (
    OfflineQueryPlanner,
    PermissionSet,
    ToolCall,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    build_catalog_registry,
)
from rag_mvp import ChromaKnowledgeStore
from rag_query import query_knowledge
from rag_reranker import CandidateReranker


SEARCH_KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
    },
    "required": ["query"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RagKnowledgeToolHandler:
    """调用现有RAG门禁，并把结果转换为稳定的工具输出。"""

    store: ChromaKnowledgeStore
    reranker: CandidateReranker
    reranker_threshold: float = 0.0153
    top_k: int = 3

    def __post_init__(self) -> None:
        if not 0 <= self.reranker_threshold <= 1:
            raise ValueError("reranker_threshold must be between 0 and 1")
        if self.top_k <= 0:
            raise ValueError("top_k must be greater than zero")

    def __call__(self, arguments: dict, trace_id: str) -> dict:
        """使用应用注入的trace_id检索；拒答时不泄露低相关候选内容。"""
        query = arguments["query"].strip()
        if not query:
            raise ToolExecutionError(
                "INVALID_ARGUMENT",
                "query must not be blank",
            )

        result = query_knowledge(
            store=self.store,
            reranker=self.reranker,
            query=query,
            reranker_threshold=self.reranker_threshold,
            top_k=self.top_k,
            trace_id=trace_id,
        )
        if not result.answerable or result.top1 is None:
            return {
                "status": "rejected",
                "answerable": False,
                "query": query,
                "content": None,
                "source": None,
                "rejection_reason": result.rejection_reason,
                "trace_id": result.trace_id,
            }

        top1 = result.top1
        return {
            "status": "success",
            "answerable": True,
            "query": query,
            "content": top1.chunk.content,
            "source": top1.chunk.source,
            "version": top1.chunk.version,
            "vector_similarity": top1.chunk.similarity,
            "reranker_score": top1.rerank_score,
            "trace_id": result.trace_id,
        }


def build_catalog_knowledge_registry(
    knowledge_handler: RagKnowledgeToolHandler,
) -> ToolRegistry:
    """在商品工具白名单中增加只读知识库检索工具。"""
    registry = build_catalog_registry()
    registry.register(
        ToolSpec(
            name="search_knowledge",
            description=(
                "Search the approved SDET knowledge base for rules, status codes, "
                "authentication, logging, and request tracing guidance."
            ),
            input_schema=SEARCH_KNOWLEDGE_SCHEMA,
            required_permission="knowledge:read",
            handler=knowledge_handler,
            timeout_seconds=10.0,
            pass_trace_id=True,
        )
    )
    return registry


class OfflineCatalogKnowledgePlanner:
    """用确定性关键词在商品工具与知识工具之间进行离线分流。"""

    KNOWLEDGE_HINTS = (
        "规则",
        "状态码",
        "鉴权",
        "令牌",
        "request_id",
        "日志",
        "为什么",
        "限制",
        "知识库",
    )

    def __init__(self, catalog_planner: OfflineQueryPlanner | None = None):
        self.catalog_planner = catalog_planner or OfflineQueryPlanner()

    def plan(
        self,
        user_text: str,
        permissions: PermissionSet,
        trace_id: str | None = None,
    ) -> ToolCall:
        """知识问题选择RAG工具，其余问题继续复用商品查询Planner。"""
        if not isinstance(user_text, str) or not user_text.strip():
            raise ToolExecutionError("INVALID_ARGUMENT", "user_text is required")

        text = user_text.strip()
        trace_id = trace_id or uuid.uuid4().hex
        normalized_text = text.lower()
        if any(hint in normalized_text for hint in self.KNOWLEDGE_HINTS):
            return ToolCall(
                tool_name="search_knowledge",
                arguments={"query": text},
                trace_id=trace_id,
                permissions=permissions,
            )
        return self.catalog_planner.plan(text, permissions, trace_id)


__all__ = [
    "SEARCH_KNOWLEDGE_SCHEMA",
    "OfflineCatalogKnowledgePlanner",
    "RagKnowledgeToolHandler",
    "build_catalog_knowledge_registry",
]
