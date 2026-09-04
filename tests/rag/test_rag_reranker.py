from app.rag.mvp import RetrievedChunk
from app.rag.reranker import CandidateReranker


def make_chunk(source: str, content: str, similarity: float) -> RetrievedChunk:
    """构造一个已经由Chroma召回的测试候选。"""
    return RetrievedChunk(
        chunk_id=source,
        content=content,
        source=source,
        version="v1",
        distance=1 - similarity,
        similarity=similarity,
    )


# 固定评分器模拟Cross-Encoder，验证重排会改变Top-1并保留原始排名。
def test_reranker_sorts_candidates_by_pair_score():
    candidates = [
        make_chunk("trace.md", "请求日志追踪", 0.40),
        make_chunk("auth.md", "未登录返回401", 0.36),
        make_chunk("catalog.md", "商品价格规则", 0.21),
    ]
    scores = {"请求日志追踪": 0.01, "未登录返回401": 0.90, "商品价格规则": 0.02}
    reranker = CandidateReranker(
        lambda pairs: [scores[document] for _query, document in pairs]
    )

    result = reranker.rerank("没有令牌返回什么？", candidates)

    assert result[0].chunk.source == "auth.md"
    assert result[0].original_rank == 2
    assert result[0].rerank_score == 0.9


# 没有召回候选时直接返回空列表，不调用无意义的模型评分。
def test_reranker_returns_empty_result_for_empty_candidates():
    reranker = CandidateReranker(lambda _pairs: [])

    assert reranker.rerank("任意问题", []) == []


# 模型分数数量和候选数量不一致属于契约错误，不能产生不完整排序。
def test_reranker_rejects_mismatched_score_count():
    reranker = CandidateReranker(lambda _pairs: [0.5])
    candidates = [
        make_chunk("a.md", "文档A", 0.4),
        make_chunk("b.md", "文档B", 0.3),
    ]

    try:
        reranker.rerank("问题", candidates)
    except ValueError as exc:
        assert str(exc) == "reranker score count must match candidate count"
    else:
        raise AssertionError("mismatched reranker scores were accepted")
