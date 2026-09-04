import uuid

from app.rag.build_index import build_persistent_index
from app.rag.mvp import ChromaKnowledgeStore, LocalHashEmbeddingFunction


# 使用临时知识目录和本地哈希Embedding，验证文件加载到磁盘索引的完整构建流程。
def test_build_persistent_index_writes_reopenable_collection(tmp_path):
    knowledge_directory = tmp_path / "knowledge"
    persist_directory = tmp_path / "chroma"
    knowledge_directory.mkdir()
    (knowledge_directory / "catalog.md").write_text(
        "商品价格必须大于零。",
        encoding="utf-8",
    )
    (knowledge_directory / "auth.md").write_text(
        "未登录访问受保护接口返回401。",
        encoding="utf-8",
    )
    collection_name = f"index-build-{uuid.uuid4().hex}"

    summary = build_persistent_index(
        knowledge_directory=knowledge_directory,
        persist_directory=persist_directory,
        collection_name=collection_name,
        embedding_function=LocalHashEmbeddingFunction(),
    )
    reopened = ChromaKnowledgeStore(
        collection_name=collection_name,
        embedding_function=LocalHashEmbeddingFunction(),
        persist_directory=persist_directory,
    )

    assert summary.document_count == 2
    assert summary.chunk_count == 2
    assert summary.persist_directory == persist_directory.resolve()
    assert reopened.count() == 2


# 没有Markdown来源时不能生成一个看似成功但实际为空的持久化知识库。
def test_build_persistent_index_rejects_empty_knowledge_directory(tmp_path):
    knowledge_directory = tmp_path / "knowledge"
    knowledge_directory.mkdir()

    try:
        build_persistent_index(
            knowledge_directory=knowledge_directory,
            persist_directory=tmp_path / "chroma",
            embedding_function=LocalHashEmbeddingFunction(),
        )
    except ValueError as exc:
        assert str(exc) == "knowledge directory does not contain Markdown files"
    else:
        raise AssertionError("empty knowledge directory was indexed")
