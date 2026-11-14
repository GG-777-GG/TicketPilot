"""测试知识检索在 SQL 中按文档可见性过滤。"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.persistence.repository import search_ready_chunks_by_cosine


# 这个函数负责取得 Repository 构造的查询，不连接真实数据库。
def build_search_statement(
    *,
    allowed_visibilities: tuple[str, ...] | None,
):
    session = MagicMock(spec=Session)
    session.execute.return_value.all.return_value = []

    arguments = {
        "query_embedding": [0.0] * EMBEDDING_DIMENSIONS,
        "embedding_model": "test-embedding",
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "top_k": 1,
        "min_similarity": 0.0,
    }
    if allowed_visibilities is not None:
        arguments["allowed_visibilities"] = allowed_visibilities

    search_ready_chunks_by_cosine(session, **arguments)
    session.execute.assert_called_once()
    return session.execute.call_args.args[0]


# 这个测试负责确认可见性是在 LIMIT 之前由 SQL 过滤，而不是查询后才删结果。
@pytest.mark.parametrize(
    "allowed_visibilities",
    [
        ("internal",),
        ("internal", "restricted"),
    ],
)
def test_search_filters_allowed_visibilities_in_sql(
    allowed_visibilities: tuple[str, ...],
) -> None:
    statement = build_search_statement(
        allowed_visibilities=allowed_visibilities
    )
    compiled = statement.compile()
    sql = str(compiled)

    assert "knowledge_documents.visibility IN" in sql
    assert sql.index("knowledge_documents.visibility IN") < sql.index("LIMIT")

    # 参数化查询仍需包含完整的权限列表，不能只检查 SQL 文本。
    bound_values = [
        value
        for name, value in compiled.params.items()
        if name.startswith("visibility_")
    ]
    assert len(bound_values) == 1
    assert tuple(bound_values[0]) == allowed_visibilities


# 这个测试负责确认旧调用者未显式传权限时只能搜索内部文档。
def test_search_defaults_to_internal_only() -> None:
    statement = build_search_statement(allowed_visibilities=None)
    compiled = statement.compile()

    bound_values = [
        value
        for name, value in compiled.params.items()
        if name.startswith("visibility_")
    ]
    assert len(bound_values) == 1
    assert tuple(bound_values[0]) == ("internal",)
