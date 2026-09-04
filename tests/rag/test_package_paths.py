"""包迁移后，默认数据路径不应随模块位置或工作目录改变。"""

from pathlib import Path

from app.rag import build_index, query, retrieval_evaluation


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_default_index_paths_still_point_to_project_root():
    assert build_index.DEFAULT_KNOWLEDGE_DIRECTORY == PROJECT_ROOT / "knowledge"
    assert build_index.DEFAULT_PERSIST_DIRECTORY == PROJECT_ROOT / "data" / "chroma"
    assert query.DEFAULT_PERSIST_DIRECTORY == PROJECT_ROOT / "data" / "chroma"


def test_evaluation_documents_path_is_independent_of_cwd(monkeypatch, tmp_path):
    received = []

    def capture_path(path):
        received.append(path)
        return []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(retrieval_evaluation, "load_knowledge_documents", capture_path)
    assert retrieval_evaluation.build_evaluation_documents() == []
    assert received == [PROJECT_ROOT / "knowledge"]
