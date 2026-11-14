"""问题重写查询列表的单元测试。"""

import pytest

from ticketpilot.knowledge.application.query_rewriting import (
    build_search_queries,
)


def test_build_search_queries_keeps_original_before_rewrite() -> None:
    # 改写只能补充检索问题，不能覆盖用户真正提出的问题。
    queries = build_search_queries(
        "  开发新功能前先确定什么？  ",
        "  完成新功能的第一步是什么？  ",
    )

    assert queries == (
        "开发新功能前先确定什么？",
        "完成新功能的第一步是什么？",
    )


def test_build_search_queries_uses_original_when_rewrite_is_missing() -> None:
    # 模型不可用或没有产生有效改写时，仍可按原问题检索。
    assert build_search_queries("原问题", None) == ("原问题",)
    assert build_search_queries("原问题", "  ") == ("原问题",)


def test_build_search_queries_does_not_repeat_identical_query() -> None:
    # 去掉两端空白后相同的改写，不应触发重复检索。
    assert build_search_queries("原问题", "  原问题  ") == ("原问题",)


def test_build_search_queries_rejects_blank_original() -> None:
    # 没有原问题时，不能让模型改写凭空变成用户的真实请求。
    with pytest.raises(ValueError, match="原问题不能为空"):
        build_search_queries("  ", "模型生成的问题")
