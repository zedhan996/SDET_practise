import uuid

from rag_mvp import (
    ChromaKnowledgeStore,
    KnowledgeDocument,
    LocalHashEmbeddingFunction,
    index_documents,
    load_knowledge_documents,
    split_document,
)


def build_test_store() -> ChromaKnowledgeStore:
    """每条测试使用独立的内存集合，避免测试数据互相污染。"""
    return ChromaKnowledgeStore(
        # EphemeralClient 在同一进程中共享内存服务，因此集合名称也必须唯一。
        collection_name=f"rag-test-{uuid.uuid4().hex}",
        embedding_function=LocalHashEmbeddingFunction(),
    )


def build_documents() -> list[KnowledgeDocument]:
    """准备带来源和版本的最小知识库样例。"""
    return [
        KnowledgeDocument(
            document_id="auth-guide",
            content="创建商品接口要求管理员权限。未登录返回 401，普通用户无权操作返回 403。",
            source="docs/auth-guide.md",
            version="v2",
        ),
        KnowledgeDocument(
            document_id="catalog-guide",
            content="商品价格必须大于 0。合法创建成功返回 201，商品名称和价格都需要通过输入校验。",
            source="docs/catalog-guide.md",
            version="v1",
        ),
    ]


# 单独验证文档切块，同时检查重叠参数和来源字段不会丢失。
def test_split_document_creates_overlapping_chunks_with_metadata():
    document = KnowledgeDocument("doc-1", "abcdefghij", "docs/example.md", "v1")

    chunks = split_document(document, chunk_size=6, overlap=2)

    assert [chunk.content for chunk in chunks] == ["abcdef", "efghij", "ij"]
    assert all(chunk.source == "docs/example.md" for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]


# 联动切分、Embedding 和 Chroma，验证知识片段可以被写入并检索。
def test_index_and_retrieve_returns_source_and_version():
    store = build_test_store()

    inserted = index_documents(store, build_documents())
    result = store.retrieve("商品价格必须大于 0", top_k=1, trace_id="rag-001")

    assert inserted == 2
    assert store.count() == 2
    assert result.trace_id == "rag-001"
    assert result.answerable is True
    assert result.rejection_reason is None
    assert len(result.hits) == 1
    assert result.hits[0].source == "docs/catalog-guide.md"
    assert result.hits[0].version == "v1"


# 验证 top_k 是返回数量上限，不会把整个知识库无条件交给后续模型。
def test_retrieve_respects_top_k():
    store = build_test_store()
    index_documents(store, build_documents())

    result = store.retrieve("接口权限和商品创建规则", top_k=1)

    assert len(result.hits) == 1


# 验证重复索引使用 upsert，不会因为同一个 chunk_id 产生重复片段。
def test_reindex_same_document_is_idempotent():
    store = build_test_store()
    documents = build_documents()

    index_documents(store, documents)
    index_documents(store, documents)

    assert store.count() == 2


# 空知识库是合法状态，检索应返回空结果而不是伪造一条知识。
def test_empty_store_returns_no_hits():
    result = build_test_store().retrieve("任意问题", trace_id="rag-empty-001")

    assert result.hits == []
    assert result.trace_id == "rag-empty-001"
    assert result.answerable is False
    assert result.rejection_reason == "EMPTY_KNOWLEDGE_BASE"


# 使用较高阈值稳定制造低相关场景，验证候选存在时也可以拒绝交给模型回答。
def test_low_similarity_result_is_rejected():
    store = ChromaKnowledgeStore(
        collection_name="rag-threshold-test",
        embedding_function=LocalHashEmbeddingFunction(),
        min_similarity=0.99,
    )
    index_documents(store, build_documents())

    result = store.retrieve("明天天气怎么样", top_k=1)

    assert len(result.hits) == 1
    assert result.answerable is False
    assert result.rejection_reason == "LOW_RELEVANCE"


# 阈值的合法范围与余弦相似度一致，避免无效配置悄悄进入检索链路。
def test_invalid_similarity_threshold_is_rejected():
    try:
        ChromaKnowledgeStore(min_similarity=1.1)
    except ValueError as exc:
        assert str(exc) == "min_similarity must be between -1 and 1"
    else:
        raise AssertionError("invalid similarity threshold was accepted")


# 输入校验属于检索层边界，避免空问题或非法 top_k 进入向量数据库。
def test_retrieve_rejects_invalid_query_arguments():
    store = build_test_store()

    try:
        store.retrieve("", top_k=0)
    except ValueError as exc:
        assert str(exc) == "query must not be empty"
    else:
        raise AssertionError("invalid query was accepted")


# 验证Markdown目录加载器会递归读取文件，并生成稳定来源、ID和版本元数据。
def test_load_knowledge_documents_reads_markdown_files(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "catalog.md").write_text("商品价格必须大于零。", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("不应进入知识库", encoding="utf-8")

    documents = load_knowledge_documents(tmp_path, version="v7")

    assert len(documents) == 1
    assert documents[0].document_id == "nested-catalog"
    assert documents[0].source == f"{tmp_path.name}/nested/catalog.md"
    assert documents[0].version == "v7"
    assert documents[0].content == "商品价格必须大于零。"


# 空Markdown会形成无意义向量，加载阶段应直接拒绝并指出问题文件。
def test_load_knowledge_documents_rejects_empty_markdown(tmp_path):
    (tmp_path / "empty.md").write_text("   ", encoding="utf-8")

    try:
        load_knowledge_documents(tmp_path)
    except ValueError as exc:
        assert "knowledge file must not be empty" in str(exc)
        assert "empty.md" in str(exc)
    else:
        raise AssertionError("empty knowledge file was accepted")


# 持久化模式重新创建Store后仍能读取原集合，证明索引不是只存在于内存。
def test_persistent_store_reopens_existing_collection(tmp_path):
    persist_directory = tmp_path / "chroma"
    collection_name = f"persistent-test-{uuid.uuid4().hex}"
    first_store = ChromaKnowledgeStore(
        collection_name=collection_name,
        embedding_function=LocalHashEmbeddingFunction(),
        persist_directory=persist_directory,
    )
    index_documents(first_store, build_documents())

    second_store = ChromaKnowledgeStore(
        collection_name=collection_name,
        embedding_function=LocalHashEmbeddingFunction(),
        persist_directory=persist_directory,
    )

    assert second_store.count() == 2
    assert any(persist_directory.iterdir())
