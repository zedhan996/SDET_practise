from rag_answer import answer_knowledge
from rag_mvp import RetrievalResult, RetrievedChunk
from rag_reranker import CandidateReranker


class StubStore:
    """返回固定候选，使编排测试不依赖Chroma和真实Embedding。"""

    def __init__(self, hits: list[RetrievedChunk]):
        self.hits = hits

    def retrieve(self, query, top_k, trace_id=None):
        return RetrievalResult(
            query=query,
            top_k=top_k,
            trace_id=trace_id or "answer-test-001",
            hits=self.hits,
            answerable=bool(self.hits),
            rejection_reason=None if self.hits else "EMPTY_KNOWLEDGE_BASE",
        )


def make_auth_hit() -> RetrievedChunk:
    """构造一个可被Reranker判定为相关的鉴权知识候选。"""
    return RetrievedChunk(
        chunk_id="auth-rule-chunk-0",
        content="未携带有效令牌时返回401。",
        source="knowledge/auth-rule.md",
        version="v1",
        distance=0.67,
        similarity=0.33,
    )


# 门禁通过时，完整编排应调用生成器并保留来源和trace_id。
def test_answer_knowledge_calls_generator_after_gate_passes():
    received_prompts = []

    def fake_generator(prompt: str) -> str:
        received_prompts.append(prompt)
        return "应该返回401。"

    answer = answer_knowledge(
        store=StubStore([make_auth_hit()]),
        reranker=CandidateReranker(lambda _pairs: [0.08]),
        generator=fake_generator,
        query="没有登录令牌时返回什么？",
        reranker_threshold=0.0153,
        trace_id="answer-test-accepted",
    )

    assert len(received_prompts) == 1
    assert answer.answerable is True
    assert answer.answer == "应该返回401。"
    assert answer.sources == ("knowledge/auth-rule.md",)
    assert answer.trace_id == "answer-test-accepted"


# 门禁拒答时，完整编排必须跳过Ollama，防止低相关知识诱发幻觉。
def test_answer_knowledge_skips_generator_after_gate_rejects():
    def forbidden_generator(_prompt: str) -> str:
        raise AssertionError("generator must not be called")

    answer = answer_knowledge(
        store=StubStore([make_auth_hit()]),
        reranker=CandidateReranker(lambda _pairs: [0.003]),
        generator=forbidden_generator,
        query="商品由哪位管理员审批？",
        reranker_threshold=0.0153,
    )

    assert answer.answerable is False
    assert answer.answer is None
    assert answer.rejection_reason == "LOW_RERANKER_SCORE"
