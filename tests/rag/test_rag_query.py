from app.rag.mvp import RetrievalResult, RetrievedChunk
from app.rag.query import query_knowledge
from app.rag.reranker import CandidateReranker


class StubStore:
    """返回预设检索结果，避免单元测试依赖Chroma和真实Embedding。"""

    def __init__(self, result: RetrievalResult):
        self.result = result

    def retrieve(self, query, top_k, trace_id=None):
        return self.result


def make_hit(source: str, content: str, similarity: float) -> RetrievedChunk:
    """构造带来源和向量相似度的召回候选。"""
    return RetrievedChunk(
        chunk_id=source,
        content=content,
        source=source,
        version="v1",
        distance=1 - similarity,
        similarity=similarity,
    )


def make_retrieval_result(hits: list[RetrievedChunk]) -> RetrievalResult:
    """构造带固定trace_id的向量召回结果。"""
    return RetrievalResult(
        query="测试问题",
        top_k=3,
        trace_id="query-test-001",
        hits=hits,
        answerable=bool(hits),
        rejection_reason=None if hits else "EMPTY_KNOWLEDGE_BASE",
    )


# 正确文档原本排第二时，Reranker应将它提升到Top1并通过拒答门禁。
def test_query_knowledge_reranks_and_allows_relevant_candidate():
    hits = [
        make_hit("knowledge/trace-rule.md", "请求追踪规则", 0.40),
        make_hit("knowledge/auth-rule.md", "未登录返回401", 0.36),
    ]
    scores = {"请求追踪规则": 0.002, "未登录返回401": 0.08}
    reranker = CandidateReranker(
        lambda pairs: [scores[document] for _query, document in pairs]
    )

    result = query_knowledge(
        store=StubStore(make_retrieval_result(hits)),
        reranker=reranker,
        query="没有登录令牌时返回什么？",
        reranker_threshold=0.0153,
    )

    assert result.answerable is True
    assert result.rejection_reason is None
    assert result.top1.chunk.source == "knowledge/auth-rule.md"
    assert result.top1.original_rank == 2
    assert result.trace_id == "query-test-001"


# 候选虽然存在但重排Top1分数过低时，系统必须拒答而不是强行返回文档。
def test_query_knowledge_rejects_low_reranker_score():
    hits = [make_hit("knowledge/catalog-rule.md", "商品价格规则", 0.45)]
    reranker = CandidateReranker(lambda _pairs: [0.003])

    result = query_knowledge(
        store=StubStore(make_retrieval_result(hits)),
        reranker=reranker,
        query="商品价格由哪位管理员审批？",
        reranker_threshold=0.0153,
    )

    assert result.answerable is False
    assert result.rejection_reason == "LOW_RERANKER_SCORE"
    assert result.top1.chunk.source == "knowledge/catalog-rule.md"


# 空知识库没有候选时应保留原始拒答原因，并且不能调用Reranker伪造结果。
def test_query_knowledge_preserves_empty_store_rejection():
    reranker = CandidateReranker(
        lambda _pairs: (_ for _ in ()).throw(AssertionError("reranker was called"))
    )

    result = query_knowledge(
        store=StubStore(make_retrieval_result([])),
        reranker=reranker,
        query="任意问题",
        reranker_threshold=0.0153,
    )

    assert result.answerable is False
    assert result.rejection_reason == "EMPTY_KNOWLEDGE_BASE"
    assert result.top1 is None


# 拒答阈值超出Sigmoid分数范围时应在检索前直接拒绝非法配置。
def test_query_knowledge_rejects_invalid_threshold():
    try:
        query_knowledge(
            store=StubStore(make_retrieval_result([])),
            reranker=CandidateReranker(lambda _pairs: []),
            query="任意问题",
            reranker_threshold=1.1,
        )
    except ValueError as exc:
        assert str(exc) == "reranker_threshold must be between 0 and 1"
    else:
        raise AssertionError("invalid reranker threshold was accepted")
