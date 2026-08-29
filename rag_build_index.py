"""从真实Markdown知识文件构建可持久化的Chroma向量索引。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_mvp import (
    ChromaKnowledgeStore,
    build_sentence_transformer_embedding,
    index_documents,
    load_knowledge_documents,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_KNOWLEDGE_DIRECTORY = PROJECT_ROOT / "knowledge"
DEFAULT_PERSIST_DIRECTORY = PROJECT_ROOT / "data" / "chroma"


@dataclass(frozen=True)
class IndexBuildSummary:
    """一次索引构建的输入数量、输出数量和保存位置。"""

    document_count: int
    chunk_count: int
    persist_directory: Path
    collection_name: str


def build_persistent_index(
    knowledge_directory: str | Path = DEFAULT_KNOWLEDGE_DIRECTORY,
    persist_directory: str | Path = DEFAULT_PERSIST_DIRECTORY,
    collection_name: str = "sdet-knowledge",
    embedding_function: Any | None = None,
) -> IndexBuildSummary:
    """加载知识文件、切块并写入磁盘上的Chroma集合。"""
    documents = load_knowledge_documents(knowledge_directory)
    if not documents:
        raise ValueError("knowledge directory does not contain Markdown files")

    persist_path = Path(persist_directory).resolve()
    store = ChromaKnowledgeStore(
        collection_name=collection_name,
        embedding_function=(
            embedding_function
            or build_sentence_transformer_embedding(local_files_only=True)
        ),
        persist_directory=persist_path,
    )
    chunk_count = index_documents(store, documents)
    return IndexBuildSummary(
        document_count=len(documents),
        chunk_count=chunk_count,
        persist_directory=persist_path,
        collection_name=collection_name,
    )


def parse_args() -> argparse.Namespace:
    """读取可选路径参数，默认使用项目内的知识目录和索引目录。"""
    parser = argparse.ArgumentParser(description="构建本地RAG知识库索引")
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIRECTORY))
    parser.add_argument("--persist-dir", default=str(DEFAULT_PERSIST_DIRECTORY))
    parser.add_argument("--collection", default="sdet-knowledge")
    return parser.parse_args()


def main() -> None:
    """执行真实Embedding索引，并打印可核验的构建摘要。"""
    args = parse_args()
    summary = build_persistent_index(
        knowledge_directory=args.knowledge_dir,
        persist_directory=args.persist_dir,
        collection_name=args.collection,
    )
    print(f"原始文档数：{summary.document_count}")
    print(f"写入Chunk数：{summary.chunk_count}")
    print(f"Chroma目录：{summary.persist_directory}")
    print(f"Collection：{summary.collection_name}")


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_KNOWLEDGE_DIRECTORY",
    "DEFAULT_PERSIST_DIRECTORY",
    "IndexBuildSummary",
    "build_persistent_index",
]
