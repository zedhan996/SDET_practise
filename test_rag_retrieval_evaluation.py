from rag_mvp import RetrievedChunk
from rag_reranker import RerankedChunk
from rag_retrieval_evaluation import (
    RerankerEvaluationResult,
    RetrievalEvaluationResult,
    build_evaluation_cases,
    calculate_ranking_metrics,
    calibrate_reranker_threshold,
    calibrate_similarity_threshold,
    evaluate_reranker_threshold,
    format_evaluation_report,
    format_reranker_threshold_report,
)


def make_result(case_id, expected_source, similarity, expected_rank=None):
    """构造不依赖 Chroma 和真实模型的阈值评测结果。"""
    candidate_sources = ("docs/a.md", "docs/b.md", "docs/c.md")
    return RetrievalEvaluationResult(
        case_id=case_id,
        category="测试样本",
        query=f"query-{case_id}",
        expected_source=expected_source,
        # 实际 Top1 始终来自候选列表，不能拿预期来源冒充检索结果。
        actual_source=candidate_sources[0],
        similarity=similarity,
        source_match=(expected_rank == 1) if expected_source else None,
        candidate_sources=candidate_sources,
        candidate_similarities=(similarity, 0.2, 0.1),
        expected_rank=expected_rank,
    )


def make_reranker_result(
    case_id,
    expected_source,
    rerank_score,
    dataset_split="calibration",
):
    """构造不加载Cross-Encoder的Reranker阈值评测结果。"""
    chunk = RetrievedChunk(
        chunk_id=f"{case_id}-chunk",
        content=f"document-{case_id}",
        source="docs/a.md",
        version="v1",
        distance=0.5,
        similarity=0.5,
    )
    return RerankerEvaluationResult(
        case_id=case_id,
        category="测试样本",
        expected_source=expected_source,
        original_top1_source="docs/a.md",
        reranked_top1_source="docs/a.md",
        original_expected_rank=1 if expected_source else None,
        reranked_expected_rank=1 if expected_source else None,
        candidates=(
            RerankedChunk(
                chunk=chunk,
                original_rank=1,
                rerank_score=rerank_score,
            ),
        ),
        dataset_split=dataset_split,
    )


# 正负样本完全分离时，阈值应选择两组边界分数的中点。
def test_calibration_uses_midpoint_when_scores_are_separated():
    results = [
        make_result("positive-1", "docs/a.md", 0.7, expected_rank=1),
        make_result("positive-2", "docs/a.md", 0.6, expected_rank=1),
        make_result("negative-1", None, 0.2),
        make_result("negative-2", None, 0.1),
    ]

    calibration = calibrate_similarity_threshold(results)

    assert calibration.threshold == 0.4
    assert calibration.accuracy == 1.0
    assert calibration.false_accepts == 0
    assert calibration.false_rejects == 0


# 缺少正样本或负样本时无法判断分界线，必须拒绝伪造阈值。
def test_calibration_requires_both_positive_and_negative_cases():
    try:
        calibrate_similarity_threshold(
            [make_result("positive-only", "docs/a.md", 0.7, expected_rank=1)]
        )
    except ValueError as exc:
        assert str(exc) == "both positive and negative evaluation cases are required"
    else:
        raise AssertionError("one-sided evaluation data was accepted")


# 报告应包含阈值、错误接受和错误拒绝，便于复制到学习记录或CI产物。
def test_report_contains_case_and_threshold_summary():
    results = [
        make_result("positive-1", "docs/a.md", 0.7, expected_rank=1),
        make_result("negative-1", None, 0.1),
    ]
    calibration = calibrate_similarity_threshold(results)
    ranking_metrics = calculate_ranking_metrics(results, top_k=3)

    report = format_evaluation_report(results, calibration, ranking_metrics)

    assert "positive-1" in report
    assert "建议演示阈值" in report
    assert "错误接受：0" in report
    assert "错误拒绝：0" in report
    assert "Recall@3" in report
    assert "应拒答 | docs/a.md | 不适用" in report


# 正确来源位于第1、第2和Top-3之外时，分别影响Hit@1、Recall@3和MRR。
def test_ranking_metrics_use_expected_source_rank():
    results = [
        make_result("rank-1", "docs/a.md", 0.8, expected_rank=1),
        make_result("rank-2", "docs/b.md", 0.7, expected_rank=2),
        make_result("miss", "docs/d.md", 0.6, expected_rank=None),
    ]

    metrics = calculate_ranking_metrics(results, top_k=3)

    assert metrics.hit_at_1 == 1 / 3
    assert metrics.recall_at_k == 2 / 3
    assert metrics.mean_reciprocal_rank == 0.5


# 正负样本分数分离时，应在最高负样本与最低正样本之间生成拒答阈值。
def test_reranker_threshold_uses_separated_top1_scores():
    results = [
        make_reranker_result("positive-1", "docs/a.md", 0.08),
        make_reranker_result("positive-2", "docs/a.md", 0.06),
        make_reranker_result("negative-1", None, 0.009),
        make_reranker_result("negative-2", None, 0.002),
    ]

    calibration = calibrate_reranker_threshold(results)

    assert calibration.threshold == 0.0345
    assert calibration.accuracy == 1.0
    assert calibration.false_accepts == 0
    assert calibration.false_rejects == 0


# 拒答门禁报告应展示阈值、每条决策和两类错误统计。
def test_reranker_threshold_report_contains_decisions():
    results = [
        make_reranker_result("positive-1", "docs/a.md", 0.08),
        make_reranker_result("negative-1", None, 0.008),
    ]
    calibration = calibrate_reranker_threshold(results)

    report = format_reranker_threshold_report(results, calibration)

    assert "Reranker建议拒答阈值：0.0440" in report
    assert "positive-1 | 0.0800 | 应回答 | 回答 | 正确" in report
    assert "negative-1 | 0.0080 | 应拒答 | 拒答 | 正确" in report
    assert "错误接受：0" in report
    assert "错误拒绝：0" in report


# 没有重排候选时，Top1分数属性应安全返回None并供门禁执行拒答。
def test_reranked_top1_score_returns_none_without_candidates():
    result = RerankerEvaluationResult(
        case_id="empty-1",
        category="空候选",
        expected_source=None,
        original_top1_source=None,
        reranked_top1_source=None,
        original_expected_rank=None,
        reranked_expected_rank=None,
        candidates=(),
    )

    assert result.reranked_top1_score is None


# 固定阈值用于验证集时不得重新调参，并应准确统计两类决策错误。
def test_fixed_reranker_threshold_evaluates_validation_cases():
    validation_results = [
        make_reranker_result("positive-pass", "docs/a.md", 0.08, "validation"),
        make_reranker_result("positive-reject", "docs/a.md", 0.05, "validation"),
        make_reranker_result("negative-accept", None, 0.07, "validation"),
        make_reranker_result("negative-pass", None, 0.01, "validation"),
    ]

    metrics = evaluate_reranker_threshold(validation_results, threshold=0.06)

    assert metrics.threshold == 0.06
    assert metrics.accuracy == 0.5
    assert metrics.false_accepts == 1
    assert metrics.false_rejects == 1


# 内置真实评测样本必须同时包含校准集和验证集，避免同集调参与打分。
def test_evaluation_cases_define_calibration_and_validation_splits():
    cases = build_evaluation_cases()

    splits = {case.dataset_split for case in cases}
    assert splits == {"calibration", "validation"}
    assert all(
        any(case.expected_source is not None for case in cases if case.dataset_split == split)
        and any(case.expected_source is None for case in cases if case.dataset_split == split)
        for split in splits
    )
