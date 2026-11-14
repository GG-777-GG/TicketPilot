"""验证检索评估指标只依赖排序结果和人工标注，不依赖模型服务。"""

from dataclasses import FrozenInstanceError

import pytest

from ticketpilot.knowledge.application.evaluation import (
    RetrievalEvalCase,
    RetrievalEvalResult,
    evaluate_retrieval_case,
    recall_at_k,
    reciprocal_rank_at_k,
)


def test_retrieval_eval_case_can_feed_both_metrics() -> None:
    # 一条题目可以标注多个正确片段；检索结果按排名排列。
    case = RetrievalEvalCase(
        case_id="feature-workflow",
        question="开发新功能前要确定什么？",
        relevant_chunk_ids=("correct-a", "correct-b"),
    )
    retrieved_ids = ["unrelated", "correct-a", "other"]

    # 指标函数接收集合；set() 将标注元组转换为去重后的集合。
    relevant_ids = set(case.relevant_chunk_ids)

    assert case.case_id == "feature-workflow"
    assert case.question == "开发新功能前要确定什么？"
    assert recall_at_k(retrieved_ids, relevant_ids, 3) == 0.5
    assert reciprocal_rank_at_k(retrieved_ids, relevant_ids, 3) == 0.5


def test_retrieval_eval_case_cannot_be_reassigned() -> None:
    case = RetrievalEvalCase("sample", "问题", ("correct",))

    # frozen=True 禁止创建后重新给字段赋值。
    with pytest.raises(FrozenInstanceError):
        case.question = "另一个问题"


def test_evaluate_retrieval_case_returns_both_scores() -> None:
    case = RetrievalEvalCase(
        case_id="feature-workflow",
        question="开发新功能前要确定什么？",
        relevant_chunk_ids=("correct-a", "correct-b"),
    )

    # 前三条只找回一个正确片段，且它排在第二位。
    result = evaluate_retrieval_case(
        case=case,
        retrieved_ids=["unrelated", "correct-a", "other"],
        k=3,
    )

    assert result == RetrievalEvalResult(
        case_id="feature-workflow",
        k=3,
        recall=0.5,
        reciprocal_rank=0.5,
    )


def test_evaluate_retrieval_case_rejects_empty_labels() -> None:
    case = RetrievalEvalCase(
        case_id="missing-label",
        question="未标注的问题",
        relevant_chunk_ids=(),
    )

    # 空标准答案不能被误报成零分，应明确提示标注有误。
    with pytest.raises(ValueError, match="必须提供至少一个正确片段 ID"):
        evaluate_retrieval_case(case=case, retrieved_ids=["A"], k=1)


@pytest.mark.parametrize(
    ("retrieved_ids", "relevant_ids", "k", "expected"),
    [
        (["A", "B", "C"], {"A"}, 1, 1.0),
        (["B", "A", "C"], {"A"}, 1, 0.0),
        (["B", "A", "C"], {"A"}, 2, 1.0),
        (["B", "C"], {"A"}, 2, 0.0),
        (["B", "A", "C"], {"A", "C"}, 2, 0.5),
        (["A", "A", "B"], {"A", "B"}, 2, 0.5),
    ],
)
def test_recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
    expected: float,
) -> None:
    # 召回率计算命中的正确片段数量，而不是检索结果总数。
    assert recall_at_k(retrieved_ids, relevant_ids, k) == expected


@pytest.mark.parametrize(
    ("retrieved_ids", "relevant_ids", "k", "expected"),
    [
        (["A", "B", "C"], {"A"}, 1, 1.0),
        (["B", "A", "C"], {"A"}, 1, 0.0),
        (["B", "A", "C"], {"A"}, 2, 0.5),
        (["B", "C"], {"A"}, 2, 0.0),
        (["B", "C", "A"], {"A", "C"}, 3, 0.5),
    ],
)
def test_reciprocal_rank_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
    expected: float,
) -> None:
    # 只看最早出现的正确片段，它排第几就返回 1 / 几。
    assert reciprocal_rank_at_k(retrieved_ids, relevant_ids, k) == expected


@pytest.mark.parametrize("metric", [recall_at_k, reciprocal_rank_at_k])
@pytest.mark.parametrize("k", [0, -1])
def test_metrics_reject_non_positive_k(metric, k: int) -> None:
    with pytest.raises(ValueError, match="k 必须大于 0"):
        metric(["A"], {"A"}, k)


@pytest.mark.parametrize("metric", [recall_at_k, reciprocal_rank_at_k])
def test_metrics_require_relevant_ids(metric) -> None:
    with pytest.raises(ValueError, match="必须提供至少一个正确片段 ID"):
        metric(["A"], set(), 1)
