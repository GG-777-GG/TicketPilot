"""测试 Retrieval Service 把可见性范围传给数据库查询。"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

import ticketpilot.knowledge.application.retrieval as retrieval
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.embeddings.base import EmbeddingBatch, EmbeddingProvider


# 这个测试负责验证主管的两种可见性都传给 Repository。
def test_retrieval_forwards_allowed_visibilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="test-embedding",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((1.0, *([0.0] * (EMBEDDING_DIMENSIONS - 1))),),
    )
    repository_search = MagicMock(return_value=())
    monkeypatch.setattr(
        retrieval,
        "search_ready_chunks_by_cosine",
        repository_search,
    )

    results = retrieval.retrieve_knowledge(
        session=session,
        question="维修保修多久？",
        embedding_provider=embedding_provider,
        allowed_visibilities=("internal", "restricted"),
    )

    assert results == ()
    assert repository_search.call_args.kwargs[
        "allowed_visibilities"
    ] == ("internal", "restricted")
