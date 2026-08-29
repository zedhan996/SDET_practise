"""组合检索门禁和Ollama生成器，提供本地RAG完整问答入口。"""

from __future__ import annotations

import argparse

from rag_generation import (
    GroundedAnswer,
    OllamaTextGenerator,
    TextGenerator,
    generate_grounded_answer,
)
from rag_mvp import ChromaKnowledgeStore, build_sentence_transformer_embedding
from rag_query import DEFAULT_PERSIST_DIRECTORY, query_knowledge
from rag_reranker import CandidateReranker, build_cross_encoder_reranker


DEFAULT_RERANKER_THRESHOLD = 0.0153


def answer_knowledge(
    store: ChromaKnowledgeStore,
    reranker: CandidateReranker,
    generator: TextGenerator,
    query: str,
    reranker_threshold: float,
    top_k: int = 3,
    trace_id: str | None = None,
) -> GroundedAnswer:
    """先执行检索门禁；只有证据合格时才调用大语言模型。"""
    query_result = query_knowledge(
        store=store,
        reranker=reranker,
        query=query,
        reranker_threshold=reranker_threshold,
        top_k=top_k,
        trace_id=trace_id,
    )
    return generate_grounded_answer(query_result, generator)


def parse_args() -> argparse.Namespace:
    """读取知识库、门禁阈值和Ollama连接参数。"""
    parser = argparse.ArgumentParser(description="使用本地Ollama执行完整RAG问答")
    parser.add_argument("query", help="需要查询的自然语言问题")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_RERANKER_THRESHOLD,
        help="通过当前评测集校准的Reranker拒答阈值",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIRECTORY))
    parser.add_argument("--collection", default="sdet-knowledge")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-timeout", type=float, default=60.0)
    parser.add_argument("--max-output-tokens", type=int, default=128)
    parser.add_argument("--trace-id")
    return parser.parse_args()


def main() -> None:
    """加载磁盘索引与本地模型，输出回答、来源和追踪ID。"""
    args = parse_args()
    store = ChromaKnowledgeStore(
        collection_name=args.collection,
        embedding_function=build_sentence_transformer_embedding(local_files_only=True),
        persist_directory=args.persist_dir,
    )
    reranker = build_cross_encoder_reranker(local_files_only=True)
    generator = OllamaTextGenerator(
        model=args.model,
        base_url=args.ollama_url,
        timeout_seconds=args.ollama_timeout,
        max_output_tokens=args.max_output_tokens,
    )
    answer = answer_knowledge(
        store=store,
        reranker=reranker,
        generator=generator,
        query=args.query,
        reranker_threshold=args.threshold,
        top_k=args.top_k,
        trace_id=args.trace_id,
    )

    print(f"trace_id：{answer.trace_id}")
    print(f"决策：{'回答' if answer.answerable else '拒答'}")
    if answer.answerable:
        print(f"回答：{answer.answer}")
        print(f"来源：{', '.join(answer.sources)}")
    else:
        print(f"拒答原因：{answer.rejection_reason}")


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_RERANKER_THRESHOLD",
    "answer_knowledge",
]
