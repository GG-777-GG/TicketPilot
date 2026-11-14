from collections.abc import Sequence
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    """一条人工标注的知识检索评估题。"""

    # 给题目一个稳定名称，方便在测试报告里定位失败项。
    case_id: str

    # 要提交给知识库检索的真实问题。
    question: str

    # 人工确认哪些片段能回答这个问题。
    # 可以有多个正确片段，所以使用 tuple 保存多个 ID。
    relevant_chunk_ids: tuple[str, ...]

def recall_at_k(
    retrieved_ids: Sequence[str],  # 检索结果的片段 ID，按相似度从高到低排列
    relevant_ids: set[str],         # 人工标注的正确片段 ID
    k: int,                          # 只检查前 k 条检索结果
) -> float:
    # k=0 或负数时，“前 k 条”没有合理意义。
    if k < 1:
        raise ValueError("k 必须大于 0")

    # 没有标准答案，就无法计算召回率。
    if not relevant_ids:
        raise ValueError("必须提供至少一个正确片段 ID")

    # [:k] 是 Python 的切片：取前 k 个元素。
    # set(...) 转成集合，便于比较，也避免重复 ID 被重复计数。
    top_ids = set(retrieved_ids[:k])

    # & 是集合交集：找出“前 k 条中真正正确的 ID”。
    found_ids = top_ids & relevant_ids

    # len(...) 是 Python 内置函数，用来计算集合元素数量。
    return len(found_ids) / len(relevant_ids)

def reciprocal_rank_at_k(
    retrieved_ids: Sequence[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    if k < 1:
        raise ValueError("k 必须大于 0")

    if not relevant_ids:
        raise ValueError("必须提供至少一个正确片段 ID")

    # enumerate 是 Python 内置函数，遍历时同时给出位置和元素。
    # start=1 表示名次从第 1 名开始，而不是默认的第 0 位。
    for rank, chunk_id in enumerate(retrieved_ids[:k], start=1):
        if chunk_id in relevant_ids:
            return 1 / rank  # 第一条正确结果排第几，就计算 1 ÷ 几。

    # 前 k 条都没有正确结果。
    return 0.0


@dataclass(frozen=True, slots=True)
class RetrievalEvalResult:
    """一道评估题的检索成绩。"""

    # 对应哪一道评估题，方便定位结果。
    case_id: str

    # 评估检索结果的前多少条。
    k: int

    # 前 k 条找回了多少比例的正确片段。
    recall: float

    # 第一条正确片段的排名倒数。
    reciprocal_rank: float


def evaluate_retrieval_case(
    *,
    # 人工准备的评估题，包含问题和正确片段 ID。
    case: RetrievalEvalCase,

    # 知识库实际检索出的片段 ID，必须按排名从高到低排列。
    retrieved_ids: Sequence[str],

    # 只评估前 k 条检索结果，例如 k=5 表示看前 5 条。
    k: int,
) -> RetrievalEvalResult:
    """计算一道题的检索成绩。"""

    # set() 是 Python 内置函数：把正确 ID 转成去重后的集合。
    relevant_ids = set(case.relevant_chunk_ids)

    return RetrievalEvalResult(
        case_id=case.case_id,
        k=k,
        recall=recall_at_k(
            retrieved_ids,
            relevant_ids,
            k,
        ),
        reciprocal_rank=reciprocal_rank_at_k(
            retrieved_ids,
            relevant_ids,
            k,
        ),
    )