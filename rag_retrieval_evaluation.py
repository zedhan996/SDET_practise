"""RAG 检索评测集和相似度阈值校准工具。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from rag_mvp import (
    ChromaKnowledgeStore,
    KnowledgeDocument,
    build_sentence_transformer_embedding,
    index_documents,
    load_knowledge_documents,
)
from rag_reranker import (
    CandidateReranker,
    RerankedChunk,
    build_cross_encoder_reranker,
)


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    """一条检索评测用例；expected_source 为空表示知识库不应回答。"""

    case_id: str
    category: str
    query: str
    expected_source: str | None
    dataset_split: str = "calibration"


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    """一条用例的实际召回来源和最高相似度。"""

    case_id: str
    category: str
    query: str
    expected_source: str | None
    actual_source: str | None
    similarity: float
    source_match: bool | None
    candidate_sources: tuple[str, ...] = ()
    candidate_similarities: tuple[float, ...] = ()
    expected_rank: int | None = None

    @property
    def expected_answerable(self) -> bool:
        """有预期来源的问题属于知识库应当回答的正样本。"""
        return self.expected_source is not None


@dataclass(frozen=True)
class ThresholdCalibration:
    """一个候选拒答阈值及其分类结果。"""

    threshold: float
    accuracy: float
    false_accepts: int
    false_rejects: int
    total: int


@dataclass(frozen=True)
class RetrievalRankingMetrics:
    """正样本在 Top-k 候选中的排序质量指标。"""

    top_k: int
    positive_cases: int
    hit_at_1: float
    recall_at_k: float
    mean_reciprocal_rank: float


@dataclass(frozen=True)
class RerankerEvaluationResult:
    """一条用例在重排前后的预期来源排名。"""

    case_id: str
    category: str
    expected_source: str | None
    original_top1_source: str | None
    reranked_top1_source: str | None
    original_expected_rank: int | None
    reranked_expected_rank: int | None
    candidates: tuple[RerankedChunk, ...]
    dataset_split: str = "calibration"

    @property
    def expected_answerable(self) -> bool:
        return self.expected_source is not None

    @property
    def reranked_top1_score(self) -> float | None:
        """返回重排后第一名的分数；没有候选时返回 None。"""
        return self.candidates[0].rerank_score if self.candidates else None


def evaluate_retrieval_cases(
    store: ChromaKnowledgeStore,
    cases: list[RetrievalEvaluationCase],
    top_k: int = 3,
) -> list[RetrievalEvaluationResult]:
    """不设置拒答阈值，收集每条问题的 Top-k 来源、排名和原始分数。"""
    if top_k <= 0:
        raise ValueError("top_k must be greater than 0")

    results: list[RetrievalEvaluationResult] = []
    for case in cases:
        retrieval = store.retrieve(
            case.query,
            top_k=top_k,
            trace_id=f"rag-eval-{case.case_id}",
        )
        best_hit = retrieval.hits[0] if retrieval.hits else None
        actual_source = best_hit.source if best_hit else None
        candidate_sources = tuple(hit.source for hit in retrieval.hits)
        candidate_similarities = tuple(hit.similarity for hit in retrieval.hits)
        expected_rank = None
        if case.expected_source is not None:
            expected_rank = next(
                (
                    index
                    for index, source in enumerate(candidate_sources, start=1)
                    if source == case.expected_source
                ),
                None,
            )
        source_match = (
            expected_rank == 1
            if case.expected_source is not None
            else None
        )
        results.append(
            RetrievalEvaluationResult(
                case_id=case.case_id,
                category=case.category,
                query=case.query,
                expected_source=case.expected_source,
                actual_source=actual_source,
                similarity=best_hit.similarity if best_hit else -1.0,
                source_match=source_match,
                candidate_sources=candidate_sources,
                candidate_similarities=candidate_similarities,
                expected_rank=expected_rank,
            )
        )
    return results


def calculate_ranking_metrics(
    results: list[RetrievalEvaluationResult],
    top_k: int,
) -> RetrievalRankingMetrics:
    """计算正确来源排第一、进入 Top-k 和平均倒数排名的比例。"""
    positive_results = [result for result in results if result.expected_answerable]
    if not positive_results:
        raise ValueError("at least one positive evaluation case is required")

    hit_at_1 = sum(result.expected_rank == 1 for result in positive_results)
    recalled_at_k = sum(
        result.expected_rank is not None and result.expected_rank <= top_k
        for result in positive_results
    )
    reciprocal_rank_total = sum(
        1 / result.expected_rank if result.expected_rank is not None else 0
        for result in positive_results
    )
    positive_count = len(positive_results)
    return RetrievalRankingMetrics(
        top_k=top_k,
        positive_cases=positive_count,
        hit_at_1=hit_at_1 / positive_count,
        recall_at_k=recalled_at_k / positive_count,
        mean_reciprocal_rank=reciprocal_rank_total / positive_count,
    )


def evaluate_reranker_cases(
    store: ChromaKnowledgeStore,
    cases: list[RetrievalEvaluationCase],
    reranker: CandidateReranker,
    top_k: int = 3,
) -> list[RerankerEvaluationResult]:
    """召回Top-k后执行重排，并保留前后排名供对比。"""
    results: list[RerankerEvaluationResult] = []
    for case in cases:
        retrieval = store.retrieve(case.query, top_k=top_k)
        reranked = reranker.rerank(case.query, retrieval.hits)
        original_sources = [hit.source for hit in retrieval.hits]
        reranked_sources = [item.chunk.source for item in reranked]

        def find_rank(sources: list[str]) -> int | None:
            if case.expected_source is None:
                return None
            return next(
                (
                    index
                    for index, source in enumerate(sources, start=1)
                    if source == case.expected_source
                ),
                None,
            )

        results.append(
            RerankerEvaluationResult(
                case_id=case.case_id,
                category=case.category,
                expected_source=case.expected_source,
                original_top1_source=original_sources[0] if original_sources else None,
                reranked_top1_source=reranked_sources[0] if reranked_sources else None,
                original_expected_rank=find_rank(original_sources),
                reranked_expected_rank=find_rank(reranked_sources),
                candidates=tuple(reranked),
                dataset_split=case.dataset_split,
            )
        )
    return results


def calculate_reranker_ranking_metrics(
    results: list[RerankerEvaluationResult],
    top_k: int,
    use_reranked_rank: bool,
) -> RetrievalRankingMetrics:
    """按原始排名或重排后排名计算同一组指标。"""
    positive_results = [result for result in results if result.expected_answerable]
    if not positive_results:
        raise ValueError("at least one positive evaluation case is required")
    ranks = [
        result.reranked_expected_rank
        if use_reranked_rank
        else result.original_expected_rank
        for result in positive_results
    ]
    count = len(ranks)
    return RetrievalRankingMetrics(
        top_k=top_k,
        positive_cases=count,
        hit_at_1=sum(rank == 1 for rank in ranks) / count,
        recall_at_k=sum(rank is not None and rank <= top_k for rank in ranks) / count,
        mean_reciprocal_rank=sum(1 / rank if rank else 0 for rank in ranks) / count,
    )


def _score_reranker_threshold(
    results: list[RerankerEvaluationResult],
    threshold: float,
) -> ThresholdCalibration:
    """统计某个Reranker阈值下的正确、错误接受和错误拒绝。"""
    false_accepts = 0
    false_rejects = 0
    correct = 0
    for result in results:
        score = result.reranked_top1_score
        # 没有候选时必须拒答，不能为了凑结果而假定一个分数。
        predicted_answerable = score is not None and score >= threshold
        if predicted_answerable == result.expected_answerable:
            correct += 1
        elif predicted_answerable:
            false_accepts += 1
        else:
            false_rejects += 1
    return ThresholdCalibration(
        threshold=round(threshold, 4),
        accuracy=correct / len(results),
        false_accepts=false_accepts,
        false_rejects=false_rejects,
        total=len(results),
    )


def calibrate_reranker_threshold(
    results: list[RerankerEvaluationResult],
) -> ThresholdCalibration:
    """使用正负样本的重排Top1分数校准演示拒答阈值。"""
    if not results:
        raise ValueError("reranker evaluation results must not be empty")

    positive_results = [result for result in results if result.expected_answerable]
    negative_results = [result for result in results if not result.expected_answerable]
    if not positive_results or not negative_results:
        raise ValueError("both positive and negative reranker cases are required")

    positive_scores = [
        result.reranked_top1_score
        for result in positive_results
        if result.reranked_top1_score is not None
    ]
    negative_scores = [
        result.reranked_top1_score
        for result in negative_results
        if result.reranked_top1_score is not None
    ]
    if not positive_scores:
        raise ValueError("positive reranker cases must include top-1 scores")

    # 分数完全分离时，在最难负样本与最弱正样本之间取中点。
    lowest_positive = min(positive_scores)
    if negative_scores and max(negative_scores) < lowest_positive:
        return _score_reranker_threshold(
            results,
            (max(negative_scores) + lowest_positive) / 2,
        )

    scores = sorted(
        result.reranked_top1_score
        for result in results
        if result.reranked_top1_score is not None
    )
    candidates = {-1.0, 1.0, *scores}
    candidates.update(
        (left + right) / 2 for left, right in zip(scores, scores[1:])
    )
    calibrations = [
        _score_reranker_threshold(results, value) for value in candidates
    ]
    # 同准确率时优先减少错误接受，避免无依据回答。
    return max(
        calibrations,
        key=lambda item: (
            item.accuracy,
            -item.false_accepts,
            -item.false_rejects,
            item.threshold,
        ),
    )


def evaluate_reranker_threshold(
    results: list[RerankerEvaluationResult],
    threshold: float,
) -> ThresholdCalibration:
    """使用已经固定的阈值评估独立样本，不再根据结果调整阈值。"""
    if not results:
        raise ValueError("reranker validation results must not be empty")
    if not 0 <= threshold <= 1:
        raise ValueError("reranker threshold must be between 0 and 1")
    return _score_reranker_threshold(results, threshold)


def format_reranker_comparison_report(
    results: list[RerankerEvaluationResult],
    before: RetrievalRankingMetrics,
    after: RetrievalRankingMetrics,
) -> str:
    """输出每条用例和整体指标的重排前后对照。"""
    lines = [
        "",
        "=== Reranker 重排对照 ===",
        "case_id | 原Top1 → 新Top1 | 预期排名 原→新",
        "-" * 100,
    ]
    for result in results:
        if result.expected_source is None:
            rank_text = "不适用"
        else:
            rank_text = f"{result.original_expected_rank} → {result.reranked_expected_rank}"
        lines.append(
            f"{result.case_id} | {result.original_top1_source} → "
            f"{result.reranked_top1_source} | {rank_text}"
        )
        candidates = ", ".join(
            f"{index}:{candidate.chunk.source}(rerank={candidate.rerank_score:.4f}, "
            f"vector={candidate.chunk.similarity:.4f}, 原排名={candidate.original_rank})"
            for index, candidate in enumerate(result.candidates, start=1)
        )
        lines.append(f"  reranked: {candidates or '无'}")
    lines.extend(
        [
            "",
            f"Hit@1：{before.hit_at_1:.2%} → {after.hit_at_1:.2%}",
            f"Recall@{after.top_k}：{before.recall_at_k:.2%} → {after.recall_at_k:.2%}",
            f"MRR：{before.mean_reciprocal_rank:.4f} → {after.mean_reciprocal_rank:.4f}",
            "说明：本节只比较候选排序；回答/拒答效果见后续门禁报告。",
        ]
    )
    return "\n".join(lines)


def format_reranker_threshold_report(
    results: list[RerankerEvaluationResult],
    calibration: ThresholdCalibration,
    title: str = "Reranker 拒答门禁",
    threshold_label: str = "Reranker建议拒答阈值",
) -> str:
    """输出重排Top1分数、拒答决策以及整体门禁效果。"""
    lines = [
        "",
        f"=== {title} ===",
        "case_id | Top1分数 | 预期 | 实际决策 | 结果",
        "-" * 80,
    ]
    for result in results:
        score = result.reranked_top1_score
        predicted_answerable = score is not None and score >= calibration.threshold
        expected_text = "应回答" if result.expected_answerable else "应拒答"
        decision_text = "回答" if predicted_answerable else "拒答"
        result_text = (
            "正确"
            if predicted_answerable == result.expected_answerable
            else "错误"
        )
        score_text = f"{score:.4f}" if score is not None else "无"
        lines.append(
            f"{result.case_id} | {score_text} | {expected_text} | "
            f"{decision_text} | {result_text}"
        )
    lines.extend(
        [
            "",
            f"{threshold_label}：{calibration.threshold:.4f}",
            f"回答/拒答准确率：{calibration.accuracy:.2%}",
            f"错误接受：{calibration.false_accepts}",
            f"错误拒绝：{calibration.false_rejects}",
            "说明：该阈值仅适用于当前模型、知识库和小样本评测集。",
        ]
    )
    return "\n".join(lines)


def _score_threshold(
    results: list[RetrievalEvaluationResult],
    threshold: float,
) -> ThresholdCalibration:
    false_accepts = 0
    false_rejects = 0
    correct = 0
    for result in results:
        predicted_answerable = result.similarity >= threshold
        if predicted_answerable == result.expected_answerable:
            correct += 1
        elif predicted_answerable:
            false_accepts += 1
        else:
            false_rejects += 1
    return ThresholdCalibration(
        threshold=round(threshold, 4),
        accuracy=correct / len(results),
        false_accepts=false_accepts,
        false_rejects=false_rejects,
        total=len(results),
    )


def calibrate_similarity_threshold(
    results: list[RetrievalEvaluationResult],
) -> ThresholdCalibration:
    """根据正负样本分数选择演示阈值；样本扩大后仍需重新校准。"""
    if not results:
        raise ValueError("evaluation results must not be empty")

    positive_scores = [
        result.similarity for result in results if result.expected_answerable
    ]
    negative_scores = [
        result.similarity for result in results if not result.expected_answerable
    ]
    if not positive_scores or not negative_scores:
        raise ValueError("both positive and negative evaluation cases are required")

    # 两组完全分离时取最大负样本和最小正样本的中点，保留最大安全间隔。
    lowest_positive = min(positive_scores)
    highest_negative = max(negative_scores)
    if highest_negative < lowest_positive:
        return _score_threshold(
            results,
            (highest_negative + lowest_positive) / 2,
        )

    # 分数重叠时遍历分数和相邻中点；同准确率下优先减少错误接受。
    scores = sorted(set(result.similarity for result in results))
    candidates = {-1.0, 1.0, *scores}
    candidates.update(
        (left + right) / 2 for left, right in zip(scores, scores[1:])
    )
    calibrations = [_score_threshold(results, value) for value in candidates]
    return max(
        calibrations,
        key=lambda item: (
            item.accuracy,
            -item.false_accepts,
            -item.false_rejects,
            item.threshold,
        ),
    )


def format_evaluation_report(
    results: list[RetrievalEvaluationResult],
    calibration: ThresholdCalibration,
    ranking_metrics: RetrievalRankingMetrics,
) -> str:
    """生成适合终端查看和复制到学习记录的纯文本报告。"""
    lines = [
        "case_id | 类别 | Top1相似度 | 预期来源 | Top1来源 | 预期排名",
        "-" * 100,
    ]
    for result in results:
        if result.expected_source is None:
            expected_rank_text = "不适用"
        elif result.expected_rank is None:
            expected_rank_text = "未进入Top-k"
        else:
            expected_rank_text = str(result.expected_rank)
        lines.append(
            " | ".join(
                [
                    result.case_id,
                    result.category,
                    f"{result.similarity:.4f}",
                    result.expected_source or "应拒答",
                    result.actual_source or "无",
                    expected_rank_text,
                ]
            )
        )
        candidates = ", ".join(
            f"{index}:{source}({similarity:.4f})"
            for index, (source, similarity) in enumerate(
                zip(result.candidate_sources, result.candidate_similarities),
                start=1,
            )
        )
        lines.append(f"  candidates: {candidates or '无'}")
    lines.extend(
        [
            "",
            f"Hit@1：{ranking_metrics.hit_at_1:.2%}",
            f"Recall@{ranking_metrics.top_k}：{ranking_metrics.recall_at_k:.2%}",
            f"MRR：{ranking_metrics.mean_reciprocal_rank:.4f}",
            f"建议演示阈值：{calibration.threshold:.4f}",
            f"回答/拒答准确率：{calibration.accuracy:.2%}",
            f"错误接受：{calibration.false_accepts}",
            f"错误拒绝：{calibration.false_rejects}",
            "说明：该阈值只适用于当前小样本，扩大或更新知识库后必须重新评测。",
        ]
    )
    return "\n".join(lines)


def build_evaluation_documents() -> list[KnowledgeDocument]:
    """从真实Markdown文件加载商品、鉴权和日志三个主题的评测知识。"""
    return load_knowledge_documents(Path(__file__).parent / "knowledge")


def build_evaluation_cases() -> list[RetrievalEvaluationCase]:
    """覆盖正常命中、语义改写、知识边界和领域外问题。"""
    return [
        RetrievalEvaluationCase(
            "positive-price-1",
            "正样本",
            "系统能不能保存负数金额的商品？",
            "knowledge/catalog-rule.md",
            "calibration",
        ),
        RetrievalEvaluationCase(
            "positive-price-2",
            "语义改写",
            "录入商品时金额有什么限制？",
            "knowledge/catalog-rule.md",
            "validation",
        ),
        RetrievalEvaluationCase(
            "positive-auth-1",
            "正样本",
            "没有登录令牌时接口应该返回什么？",
            "knowledge/auth-rule.md",
            "calibration",
        ),
        RetrievalEvaluationCase(
            "positive-auth-2",
            "语义改写",
            "用户身份正确但是权限不够是什么状态码？",
            "knowledge/auth-rule.md",
            "validation",
        ),
        RetrievalEvaluationCase(
            "positive-trace-1",
            "正样本",
            "如何关联一次请求的响应头和服务端日志？",
            "knowledge/trace-rule.md",
            "calibration",
        ),
        RetrievalEvaluationCase(
            "boundary-price-1",
            "边界样本",
            "商品价格由哪位管理员审批？",
            None,
            "calibration",
        ),
        RetrievalEvaluationCase(
            "boundary-auth-1",
            "边界样本",
            "登录令牌应该每隔多少天刷新？",
            None,
            "validation",
        ),
        RetrievalEvaluationCase(
            "negative-weather-1",
            "领域外",
            "明天天气怎么样？",
            None,
            "calibration",
        ),
        RetrievalEvaluationCase(
            "negative-python-1",
            "领域外",
            "Python 列表应该怎么排序？",
            None,
            "validation",
        ),
    ]


def main() -> None:
    """显式启用后加载本地真实模型并打印阈值校准报告。"""
    if os.getenv("RUN_RAG_INTEGRATION") != "1":
        raise SystemExit("请先设置 RUN_RAG_INTEGRATION=1 再运行真实检索评测")

    store = ChromaKnowledgeStore(
        collection_name=f"rag-evaluation-{uuid.uuid4().hex}",
        embedding_function=build_sentence_transformer_embedding(local_files_only=True),
    )
    index_documents(store, build_evaluation_documents())
    top_k = 3
    results = evaluate_retrieval_cases(store, build_evaluation_cases(), top_k=top_k)
    calibration = calibrate_similarity_threshold(results)
    ranking_metrics = calculate_ranking_metrics(results, top_k=top_k)
    print(format_evaluation_report(results, calibration, ranking_metrics))

    if os.getenv("RUN_RAG_RERANKER") == "1":
        reranker = build_cross_encoder_reranker(local_files_only=True)
        reranked_results = evaluate_reranker_cases(
            store,
            build_evaluation_cases(),
            reranker,
            top_k=top_k,
        )
        before = calculate_reranker_ranking_metrics(
            reranked_results,
            top_k=top_k,
            use_reranked_rank=False,
        )
        after = calculate_reranker_ranking_metrics(
            reranked_results,
            top_k=top_k,
            use_reranked_rank=True,
        )
        print(format_reranker_comparison_report(reranked_results, before, after))
        calibration_results = [
            result
            for result in reranked_results
            if result.dataset_split == "calibration"
        ]
        validation_results = [
            result
            for result in reranked_results
            if result.dataset_split == "validation"
        ]
        reranker_calibration = calibrate_reranker_threshold(calibration_results)
        print(
            format_reranker_threshold_report(
                calibration_results,
                reranker_calibration,
                title="Reranker 阈值校准集",
            )
        )
        validation_metrics = evaluate_reranker_threshold(
            validation_results,
            reranker_calibration.threshold,
        )
        print(
            format_reranker_threshold_report(
                validation_results,
                validation_metrics,
                title="Reranker 独立验证集",
                threshold_label="使用固定拒答阈值",
            )
        )


if __name__ == "__main__":
    main()


__all__ = [
    "RetrievalEvaluationCase",
    "RetrievalEvaluationResult",
    "RetrievalRankingMetrics",
    "RerankerEvaluationResult",
    "ThresholdCalibration",
    "build_evaluation_cases",
    "build_evaluation_documents",
    "calibrate_reranker_threshold",
    "calibrate_similarity_threshold",
    "calculate_reranker_ranking_metrics",
    "calculate_ranking_metrics",
    "evaluate_reranker_cases",
    "evaluate_reranker_threshold",
    "evaluate_retrieval_cases",
    "format_evaluation_report",
    "format_reranker_comparison_report",
    "format_reranker_threshold_report",
]
