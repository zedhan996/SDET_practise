"""查询持久化RAG知识库，并执行Top-k重排和拒答门禁。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .mvp import (
    ChromaKnowledgeStore,
    build_sentence_transformer_embedding,
)
from .reranker import (
    CandidateReranker,
    RerankedChunk,
    build_cross_encoder_reranker,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSIST_DIRECTORY = PROJECT_ROOT / "data" / "chroma"


@dataclass(frozen=True)
class KnowledgeQueryResult:
    """一次知识查询的最终决策、追踪ID和重排候选。"""

    query: str
    trace_id: str
    answerable: bool
    rejection_reason: str | None
    candidates: tuple[RerankedChunk, ...]

    @property
    def top1(self) -> RerankedChunk | None:
        """返回门禁实际判断的重排第一名；没有候选时返回None。"""
        return self.candidates[0] if self.candidates else None


def query_knowledge(
    store: ChromaKnowledgeStore,
    reranker: CandidateReranker,
    query: str,
    reranker_threshold: float,
    top_k: int = 3,
    trace_id: str | None = None,
) -> KnowledgeQueryResult:
    """执行向量召回、候选重排和固定阈值回答门禁。"""
    if not 0 <= reranker_threshold <= 1:
        raise ValueError("reranker_threshold must be between 0 and 1")

    retrieval = store.retrieve(
        query=query,
        top_k=top_k,
        trace_id=trace_id,
    )
    if not retrieval.hits:
        return KnowledgeQueryResult(
            query=query,
            trace_id=retrieval.trace_id,
            answerable=False,
            rejection_reason=retrieval.rejection_reason or "NO_RELEVANT_CONTEXT",
            candidates=(),
        )

    candidates = tuple(reranker.rerank(query, retrieval.hits))
    if not candidates:
        return KnowledgeQueryResult(
            query=query,
            trace_id=retrieval.trace_id,
            answerable=False,
            rejection_reason="NO_RERANKED_CANDIDATE",
            candidates=(),
        )

    answerable = candidates[0].rerank_score >= reranker_threshold
    return KnowledgeQueryResult(
        query=query,
        trace_id=retrieval.trace_id,
        answerable=answerable,
        rejection_reason=None if answerable else "LOW_RERANKER_SCORE",
        candidates=candidates,
    )


def parse_args() -> argparse.Namespace:
    """读取问题、阈值和持久化索引位置。"""
    parser = argparse.ArgumentParser(description="查询本地持久化RAG知识库")
    parser.add_argument("query", help="需要查询的自然语言问题")
    parser.add_argument(
        "--threshold",
        type=float,
        required=True,
        help="通过评测集校准的Reranker拒答阈值",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIRECTORY))
    parser.add_argument("--collection", default="sdet-knowledge")
    return parser.parse_args()


def main() -> None:
    """加载本地模型和磁盘索引，输出可追溯的回答或拒答决策。"""
    args = parse_args()
    store = ChromaKnowledgeStore(
        collection_name=args.collection,
        embedding_function=build_sentence_transformer_embedding(local_files_only=True),
        persist_directory=args.persist_dir,
    )
    reranker = build_cross_encoder_reranker(local_files_only=True)
    result = query_knowledge(
        store=store,
        reranker=reranker,
        query=args.query,
        reranker_threshold=args.threshold,
        top_k=args.top_k,
    )

    print(f"trace_id：{result.trace_id}")
    print(f"决策：{'回答' if result.answerable else '拒答'}")
    print(f"拒答原因：{result.rejection_reason or '无'}")
    if result.top1 is not None:
        print(f"Top1来源：{result.top1.chunk.source}")
        print(f"向量相似度：{result.top1.chunk.similarity:.4f}")
        print(f"Reranker分数：{result.top1.rerank_score:.4f}")
        print(f"内容：{result.top1.chunk.content}")


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_PERSIST_DIRECTORY",
    "KnowledgeQueryResult",
    "query_knowledge",
]
