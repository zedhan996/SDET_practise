"""版本化行为评测使用的固定依赖；不连接真实商品数据库、向量库或检索模型。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_mvp import (
    GET_ITEM_SCHEMA,
    SEARCH_ITEMS_SCHEMA,
    ToolCallingAgent,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
)
from agent_rag import (
    SEARCH_KNOWLEDGE_SCHEMA,
    OfflineCatalogKnowledgePlanner,
    RagKnowledgeToolHandler,
)
from rag_mvp import RetrievalResult, RetrievedChunk
from rag_reranker import CandidateReranker


FIXTURE_VERSION = "offline-v1"
FIXTURE_PROFILES = frozenset({
    "catalog", "auth", "trace", "empty", "low_score", "tool_error", "timeout",
    "unknown_tool", "forged_permissions", "bad_argument_type",
})

# 这是冻结的测试数据，不依赖dev.db当前是否为空，也不读取期望答案生成结果。
CATALOG = (
    {"id": 101, "name": "iPhone 15", "price": 5999.0, "category": "phone"},
    {"id": 102, "name": "MacBook Pro", "price": 12999.0, "category": "computer"},
    {"id": 103, "name": "AirPods Pro", "price": 1899.0, "category": "accessory"},
)
DOCUMENT_SNAPSHOTS = {
    "knowledge/auth-rule.md": "未携带或携带无效的 JWT Bearer Token 时返回 401；身份有效但权限不足时返回 403。",
    "knowledge/catalog-rule.md": "创建商品时，商品价格必须大于 0，不允许录入零或负数价格。",
    "knowledge/trace-rule.md": "每次请求使用 request_id 关联响应头、应用日志和错误堆栈，辅助 RCA 定位问题。",
}

# 每项分别是来源、向量相似度、重排分数；故意保留向量Top1不正确的情况。
RAG_PROFILES = {
    "auth": (
        ("knowledge/trace-rule.md", 0.4015, 0.0029),
        ("knowledge/auth-rule.md", 0.3615, 0.0684),
    ),
    "trace": (
        ("knowledge/catalog-rule.md", 0.50, 0.002),
        ("knowledge/trace-rule.md", 0.40, 0.08),
    ),
    "empty": (),
    "low_score": (("knowledge/catalog-rule.md", 0.45, 0.003),),
}
INJECTED_PLANS = {
    "unknown_tool": {"tool_name": "delete_all_items", "arguments": {}},
    "forged_permissions": {
        "tool_name": "get_item",
        "arguments": {"item_id": 101},
        "permissions": ["catalog:read"],
    },
    "bad_argument_type": {"tool_name": "get_item", "arguments": {"item_id": "101"}},
}


class ScriptedPlanner:
    """注入指定的恶意或错误模型输出，只评测后端边界，不评测模型理解能力。"""

    def __init__(self, output: dict[str, Any]):
        self.output = output

    def plan(self, user_text, permissions, trace_id=None):
        return deepcopy(self.output)


class ScriptedKnowledgeStore:
    """返回固定候选快照；真实重排包装器和拒答门禁仍会执行。"""

    def __init__(self, entries):
        self.entries = entries

    def retrieve(self, query, top_k, trace_id=None):
        hits = [
            RetrievedChunk(
                chunk_id=f"fixture-{index}",
                content=DOCUMENT_SNAPSHOTS[source],
                source=source,
                version=FIXTURE_VERSION,
                distance=1 - similarity,
                similarity=similarity,
            )
            for index, (source, similarity, _score) in enumerate(self.entries[:top_k])
        ]
        return RetrievalResult(
            query=query,
            top_k=top_k,
            trace_id=trace_id,
            hits=hits,
            answerable=bool(hits),
            rejection_reason=None if hits else "EMPTY_KNOWLEDGE_BASE",
        )


@dataclass
class EvaluationEnvironment:
    """保存本条用例的Agent和实际进入处理函数的记录，避免用例间共享状态。"""

    agent: ToolCallingAgent
    handler_calls: list[str]
    planner_kind: str


def build_evaluation_environment(profile: str, planner_mode: str = "offline") -> EvaluationEnvironment:
    """只接收依赖配置，不接收case的expected字段，避免按答案构造执行结果。"""
    if profile not in FIXTURE_PROFILES:
        raise ValueError(f"unknown fixture profile: {profile}")
    if planner_mode not in {"offline", "ollama"}:
        raise ValueError("planner mode must be offline or ollama")
    handler_calls: list[str] = []
    items = deepcopy(CATALOG)
    entries = RAG_PROFILES.get(profile, RAG_PROFILES["auth"])
    knowledge_handler = RagKnowledgeToolHandler(
        store=ScriptedKnowledgeStore(entries),
        reranker=CandidateReranker(lambda pairs: [row[2] for row in entries[:len(pairs)]]),
    )

    def search_items(arguments):
        handler_calls.append("search_items")
        keyword = arguments.get("keyword")
        max_price = arguments.get("max_price")
        if max_price is not None and max_price <= 0:
            raise ToolExecutionError("INVALID_ARGUMENT", "max_price must be greater than 0")
        data = [
            deepcopy(item) for item in items
            if (not keyword or keyword.casefold() in item["name"].casefold())
            and (max_price is None or item["price"] <= max_price)
        ]
        return {"status": "success", "total": len(data), "data": data}

    def get_item(arguments):
        handler_calls.append("get_item")
        item = next((item for item in items if item["id"] == arguments["item_id"]), None)
        if item is None:
            raise ToolExecutionError("ITEM_NOT_FOUND", f"Item {arguments['item_id']} not found")
        return {"status": "success", "data": deepcopy(item)}

    def search_knowledge(arguments, trace_id):
        handler_calls.append("search_knowledge")
        if profile == "tool_error":
            raise RuntimeError("评测注入：检索依赖故障")
        if profile == "timeout":
            # 这里只注入超时错误，不证明真实等待时长，也不代表运行中的线程被终止。
            raise ToolExecutionError("TIMEOUT", "评测注入：检索依赖超时")
        return knowledge_handler(arguments, trace_id)

    registry = ToolRegistry([
        ToolSpec("search_items", "按名称关键词和最高价格搜索商品。", SEARCH_ITEMS_SCHEMA,
                 "catalog:read", search_items),
        ToolSpec("get_item", "根据数字ID查询商品详情。", GET_ITEM_SCHEMA,
                 "catalog:read", get_item),
        ToolSpec("search_knowledge", "检索鉴权、状态码、日志和商品规则知识库。", SEARCH_KNOWLEDGE_SCHEMA,
                 "knowledge:read", search_knowledge, pass_trace_id=True),
    ])
    if profile in INJECTED_PLANS:
        planner = ScriptedPlanner(INJECTED_PLANS[profile])
        planner_kind = "injected_output"
    elif planner_mode == "ollama":
        from agent_ollama import OllamaToolPlanner

        planner = OllamaToolPlanner.from_registry(registry)
        planner_kind = "ollama"
    else:
        planner = OfflineCatalogKnowledgePlanner()
        planner_kind = "offline_rules"
    return EvaluationEnvironment(
        agent=ToolCallingAgent(planner=planner, registry=registry),
        handler_calls=handler_calls,
        planner_kind=planner_kind,
    )
