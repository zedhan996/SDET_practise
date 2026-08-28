import os

import pytest

from rag_mvp import (
    ChromaKnowledgeStore,
    KnowledgeDocument,
    build_sentence_transformer_embedding,
    index_documents,
)


# 真实模型测试需要本地模型缓存，默认跳过以保持普通测试和 CI 快速稳定。
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_RAG_INTEGRATION") != "1",
        reason="设置 RUN_RAG_INTEGRATION=1 后才运行真实语义模型测试",
    ),
]


@pytest.fixture(scope="module")
def semantic_store() -> ChromaKnowledgeStore:
    """模型在模块级夹具中只加载一次，供本文件所有真实语义测试复用。"""
    store = ChromaKnowledgeStore(
        collection_name="rag-semantic-test",
        embedding_function=build_sentence_transformer_embedding(local_files_only=True),
    )
    index_documents(
        store,
        [
            KnowledgeDocument(
                document_id="catalog-rule",
                content="创建商品时，商品价格必须大于 0，不允许录入零或负数价格。",
                source="docs/catalog-rule.md",
                version="v1",
            ),
            KnowledgeDocument(
                document_id="auth-rule",
                content="管理员接口要求携带有效的 JWT Bearer Token，权限不足返回 403。",
                source="docs/auth-rule.md",
                version="v1",
            ),
        ],
    )
    return store


# 验证中文语义改写不是依赖原文逐字匹配，也能召回正确的价格规则。
def test_real_embedding_retrieves_semantically_related_chinese_chunk(semantic_store):
    result = semantic_store.retrieve("系统能不能保存负数金额的商品？", top_k=1)

    assert result.answerable is True
    assert result.hits[0].source == "docs/catalog-rule.md"
    assert result.hits[0].similarity > 0


# 比较正负问题分数，为后续通过评测集校准拒答阈值提供证据。
def test_related_query_scores_higher_than_out_of_domain_query(semantic_store):
    related = semantic_store.retrieve("商品金额有什么输入限制？", top_k=1)
    unrelated = semantic_store.retrieve("明天天气怎么样？", top_k=1)

    assert related.hits[0].source == "docs/catalog-rule.md"
    assert related.hits[0].similarity > unrelated.hits[0].similarity
