"""组合用户原问题和可选的检索改写问题。"""
# Protocol 定义“重写器需要提供什么方法”，不负责实际调用模型。
from typing import Protocol

def build_search_queries(
    # 用户真正提出的问题，必须保留。
    original_question: str,

    # 重写器生成的检索问题；失败或未启用时可以是 None。
    rewritten_question: str | None,
) -> tuple[str, ...]:
    """返回用于检索的问题，原问题始终排第一。"""

    # strip() 是字符串的方法，删除两端的空格和换行。
    original = original_question.strip()
    if not original:
        raise ValueError("原问题不能为空")

    # 没有有效改写时，只使用原问题检索。
    if rewritten_question is None:
        return (original,)

    rewritten = rewritten_question.strip()
    if not rewritten or rewritten == original:
        return (original,)

    # tuple 保存两个检索问题，避免后续代码意外修改它们。
    return (original, rewritten)

class QueryRewriter(Protocol):
    """问题重写器的统一接口。"""

    def rewrite(
        self,
        # 原始问题；重写器只能生成检索用的新说法。
        question: str,
    ) -> str | None:
        # None 表示没有得到可用改写，后续仍可用原问题检索。
        ...