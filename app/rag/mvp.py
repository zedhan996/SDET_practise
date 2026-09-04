"""RAG（检索增强生成）最小链路。

本模块只负责文档切分、向量写入和相似度检索，不负责调用大语言模型生成答案。
这样可以先独立验证“检索是否找到正确资料”，再接入生成模型。
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import chromadb
from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import (
    DefaultEmbeddingFunction,
    SentenceTransformerEmbeddingFunction,
)


DEFAULT_SENTENCE_TRANSFORMER_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


@dataclass(frozen=True)
class KnowledgeDocument:
    """一份待进入知识库的原始文档。"""

    document_id: str
    content: str
    source: str
    version: str = "1"


@dataclass(frozen=True)
class DocumentChunk:
    """文档切分后的最小检索单元。"""

    chunk_id: str
    content: str
    source: str
    version: str
    chunk_index: int


@dataclass(frozen=True)
class RetrievedChunk:
    """一次检索命中的内容和可追溯信息。"""

    chunk_id: str
    content: str
    source: str
    version: str
    distance: float
    similarity: float


@dataclass(frozen=True)
class RetrievalResult:
    """检索结果，trace_id 用于关联后续 Agent 或接口日志。"""

    query: str
    top_k: int
    trace_id: str
    hits: list[RetrievedChunk]
    answerable: bool
    rejection_reason: str | None = None


class LocalHashEmbeddingFunction(EmbeddingFunction[list[str]]):
    """教学用的确定性向量函数，不依赖模型下载或外部服务。

    它适合测试 Chroma 的写入、检索和评测流程，不代表生产级语义 Embedding。
    后续可以替换为 Sentence Transformers，RAG 存储和检索接口无需改变。
    """

    def __init__(self, dimensions: int = 64):
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8")
        self.dimensions = dimensions

    @staticmethod
    def name() -> str:
        return "local_hash_embedding"

    def get_config(self) -> dict[str, Any]:
        return {"dimensions": self.dimensions}

    @staticmethod
    def build_from_config(config: Mapping[str, Any]) -> "LocalHashEmbeddingFunction":
        return LocalHashEmbeddingFunction(int(config["dimensions"]))

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in input:
            vector = [0.0] * self.dimensions
            normalized = " ".join(text.lower().split())
            # 字符和相邻字符共同参与哈希，避免只按单个词做完全相同的匹配。
            features = [normalized[index : index + 2] for index in range(len(normalized))]
            features.extend(normalized[index] for index in range(len(normalized)))
            for feature in features:
                digest = hashlib.sha256(feature.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[bucket] += 1.0
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector] if norm else vector)
        return vectors


def build_sentence_transformer_embedding(
    model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    device: str = "cpu",
    local_files_only: bool = False,
) -> SentenceTransformerEmbeddingFunction:
    """构建真实语义 Embedding，并在同一进程中按模型名称复用模型对象。"""
    return SentenceTransformerEmbeddingFunction(
        model_name=model_name,
        device=device,
        normalize_embeddings=True,
        local_files_only=local_files_only,
    )


def load_knowledge_documents(
    directory: str | Path,
    version: str = "v1",
) -> list[KnowledgeDocument]:
    """从目录递归读取Markdown文件，并转换为可索引的原始知识文档。"""
    knowledge_directory = Path(directory)
    if not knowledge_directory.exists():
        raise FileNotFoundError(f"knowledge directory not found: {knowledge_directory}")
    if not knowledge_directory.is_dir():
        raise NotADirectoryError(
            f"knowledge path is not a directory: {knowledge_directory}"
        )

    documents: list[KnowledgeDocument] = []
    for file_path in sorted(knowledge_directory.rglob("*.md")):
        relative_path = file_path.relative_to(knowledge_directory)
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"knowledge file must not be empty: {relative_path}")

        # 使用相对路径生成稳定ID；source保留知识目录名，便于报告追踪原文件。
        document_id = relative_path.with_suffix("").as_posix().replace("/", "-")
        source = f"{knowledge_directory.name}/{relative_path.as_posix()}"
        documents.append(
            KnowledgeDocument(
                document_id=document_id,
                content=content,
                source=source,
                version=version,
            )
        )
    return documents


def split_document(
    document: KnowledgeDocument,
    chunk_size: int = 160,
    overlap: int = 20,
) -> list[DocumentChunk]:
    """按字符切分文档，并保留少量重叠内容防止上下文在边界处断开。"""
    if not document.document_id.strip() or not document.source.strip():
        raise ValueError("document_id and source are required")
    if not document.content.strip():
        raise ValueError("document content must not be empty")
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller")

    chunks: list[DocumentChunk] = []
    step = chunk_size - overlap
    for index, start in enumerate(range(0, len(document.content), step)):
        content = document.content[start : start + chunk_size].strip()
        if content:
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{document.document_id}-chunk-{index}",
                    content=content,
                    source=document.source,
                    version=document.version,
                    chunk_index=index,
                )
            )
    return chunks


class ChromaKnowledgeStore:
    """Chroma 的知识片段存储和检索封装。"""

    def __init__(
        self,
        collection_name: str = "sdet-knowledge",
        client: Any | None = None,
        embedding_function: EmbeddingFunction[list[str]] | None = None,
        min_similarity: float | None = None,
        persist_directory: str | Path | None = None,
    ):
        if min_similarity is not None and not -1.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between -1 and 1")
        if client is not None and persist_directory is not None:
            raise ValueError("client and persist_directory cannot be used together")
        self.min_similarity = min_similarity
        self.persist_directory = (
            Path(persist_directory).resolve() if persist_directory is not None else None
        )
        if client is not None:
            self.client = client
        elif self.persist_directory is not None:
            self.client = chromadb.PersistentClient(path=str(self.persist_directory))
        else:
            self.client = chromadb.EphemeralClient()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function or DefaultEmbeddingFunction(),
            configuration={"hnsw": {"space": "cosine"}},
        )

    def upsert_chunks(self, chunks: Iterable[DocumentChunk]) -> int:
        """写入或更新文档片段，重复 chunk_id 不会产生重复记录。"""
        chunk_list = list(chunks)
        if not chunk_list:
            return 0
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunk_list],
            documents=[chunk.content for chunk in chunk_list],
            metadatas=[
                {
                    "source": chunk.source,
                    "version": chunk.version,
                    "chunk_index": chunk.chunk_index,
                }
                for chunk in chunk_list
            ],
        )
        return len(chunk_list)

    def count(self) -> int:
        """返回当前知识库中实际保存的片段数量。"""
        return self.collection.count()

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        trace_id: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        """检索最相关片段，并保留距离和来源信息供断言及 RCA 使用。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")

        trace_id = trace_id or uuid.uuid4().hex
        if self.count() == 0:
            return RetrievalResult(
                query=query,
                top_k=top_k,
                trace_id=trace_id,
                hits=[],
                answerable=False,
                rejection_reason="EMPTY_KNOWLEDGE_BASE",
            )

        response = self.collection.query(
            query_texts=[query.strip()],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        documents = response.get("documents", [[]])[0]
        metadatas = response.get("metadatas", [[]])[0]
        distances = response.get("distances", [[]])[0]
        ids = response.get("ids", [[]])[0]
        hits = [
            RetrievedChunk(
                chunk_id=chunk_id,
                content=content,
                source=metadata["source"],
                version=metadata["version"],
                distance=float(distance),
                # 当前集合明确使用 cosine，因此 similarity = 1 - distance。
                similarity=1.0 - float(distance),
            )
            for chunk_id, content, metadata, distance in zip(
                ids, documents, metadatas, distances
            )
        ]
        if not hits:
            return RetrievalResult(
                query=query,
                top_k=top_k,
                trace_id=trace_id,
                hits=[],
                answerable=False,
                rejection_reason="NO_RELEVANT_CONTEXT",
            )

        # 阈值必须由评测集校准；未配置时只返回候选，不擅自使用经验值拒答。
        if self.min_similarity is not None and hits[0].similarity < self.min_similarity:
            return RetrievalResult(
                query=query,
                top_k=top_k,
                trace_id=trace_id,
                hits=hits,
                answerable=False,
                rejection_reason="LOW_RELEVANCE",
            )

        return RetrievalResult(
            query=query,
            top_k=top_k,
            trace_id=trace_id,
            hits=hits,
            answerable=True,
        )


def index_documents(
    store: ChromaKnowledgeStore,
    documents: Sequence[KnowledgeDocument],
    chunk_size: int = 160,
    overlap: int = 20,
) -> int:
    """完成原始文档到 Chroma 的切分、编号和写入。"""
    chunks = [
        chunk
        for document in documents
        for chunk in split_document(document, chunk_size, overlap)
    ]
    return store.upsert_chunks(chunks)


__all__ = [
    "DEFAULT_SENTENCE_TRANSFORMER_MODEL",
    "ChromaKnowledgeStore",
    "DocumentChunk",
    "KnowledgeDocument",
    "LocalHashEmbeddingFunction",
    "RetrievedChunk",
    "RetrievalResult",
    "build_sentence_transformer_embedding",
    "index_documents",
    "load_knowledge_documents",
    "split_document",
]
