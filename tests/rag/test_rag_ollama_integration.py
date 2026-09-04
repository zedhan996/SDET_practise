import os

import pytest

from app.rag.answer import answer_knowledge
from app.rag.generation import OllamaTextGenerator
from app.rag.mvp import ChromaKnowledgeStore, build_sentence_transformer_embedding
from app.rag.query import DEFAULT_PERSIST_DIRECTORY
from app.rag.reranker import build_cross_encoder_reranker


# 该测试会加载两个真实检索模型并调用本机Ollama，普通测试和CI默认跳过。
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_OLLAMA_INTEGRATION") != "1",
        reason="设置 RUN_OLLAMA_INTEGRATION=1 后才运行真实Ollama全链路测试",
    ),
]


# 验证Markdown索引、Embedding、Reranker、拒答门禁和Qwen生成的完整链路。
def test_real_rag_chain_answers_auth_question_with_ollama():
    if not DEFAULT_PERSIST_DIRECTORY.exists():
        pytest.fail("缺少data/chroma持久化索引，请先运行 python -m app.rag.build_index")

    store = ChromaKnowledgeStore(
        collection_name="sdet-knowledge",
        embedding_function=build_sentence_transformer_embedding(local_files_only=True),
        persist_directory=DEFAULT_PERSIST_DIRECTORY,
    )
    reranker = build_cross_encoder_reranker(local_files_only=True)
    generator = OllamaTextGenerator(
        model="qwen3:4b-instruct",
        timeout_seconds=60,
        max_output_tokens=128,
    )

    answer = answer_knowledge(
        store=store,
        reranker=reranker,
        generator=generator,
        query="没有登录令牌时接口应该返回什么？",
        reranker_threshold=0.0153,
        top_k=3,
        trace_id="ollama-integration-001",
    )

    assert answer.answerable is True
    assert answer.answer is not None
    assert "401" in answer.answer
    assert answer.sources == ("knowledge/auth-rule.md",)
    assert "<think>" not in answer.answer.lower()
