"""RAG Top-k 候选重排序器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from rag_mvp import RetrievedChunk


DEFAULT_RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
PairScorer = Callable[[list[tuple[str, str]]], Sequence[float]]


@dataclass(frozen=True)
class RerankedChunk:
    """保留原始向量排名，并增加 Cross-Encoder 重排分数。"""

    chunk: RetrievedChunk
    original_rank: int
    rerank_score: float


class CandidateReranker:
    """只对检索阶段已经召回的少量候选进行二次排序。"""

    def __init__(self, scorer: PairScorer):
        self.scorer = scorer

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
    ) -> list[RerankedChunk]:
        """把问题与每个候选成对评分，并按分数从高到低排序。"""
        if not query.strip():
            raise ValueError("query must not be empty")
        if not candidates:
            return []

        pairs = [(query, candidate.content) for candidate in candidates]
        scores = list(self.scorer(pairs))
        if len(scores) != len(candidates):
            raise ValueError("reranker score count must match candidate count")

        reranked = [
            RerankedChunk(
                chunk=candidate,
                original_rank=index,
                rerank_score=float(score),
            )
            for index, (candidate, score) in enumerate(
                zip(candidates, scores),
                start=1,
            )
        ]
        return sorted(reranked, key=lambda item: item.rerank_score, reverse=True)


def build_cross_encoder_reranker(
    model_name: str = DEFAULT_RERANKER_MODEL,
    device: str = "cpu",
    local_files_only: bool = False,
) -> CandidateReranker:
    """加载真实多语言 Cross-Encoder，并把输出限制到0至1方便观察。"""
    import torch
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(
        model_name,
        device=device,
        local_files_only=local_files_only,
        activation_fn=torch.nn.Sigmoid(),
    )
    return CandidateReranker(model.predict)


__all__ = [
    "DEFAULT_RERANKER_MODEL",
    "CandidateReranker",
    "RerankedChunk",
    "build_cross_encoder_reranker",
]
