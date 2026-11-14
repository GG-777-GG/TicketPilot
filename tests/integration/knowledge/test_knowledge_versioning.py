"""使用真实临时数据库测试历史版本恢复业务。"""

from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
    build_knowledge_document_version,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)
from ticketpilot.knowledge.application.versioning import (
    restore_document_version,
)


# 验证恢复历史内容会创建递增的新版本并保留审计历史。
def test_restore_document_version_creates_new_current_version(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        document_id="restore-version-document",
        created_by=database_user.id,
        title="当前第 3 版",
        raw_content="当前第 3 版正文。",
        embedding_model="old-current-model",
    )
    document.version = 3
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="current-version-chunk",
            content="当前第 3 版正文。",
        )
    )
    database_session.add(
        document
    )
    database_session.flush()

    database_session.add_all(
        [
            build_knowledge_document_version(
                version_id="restore-history-1",
                document_id=document.id,
                version=1,
                title="历史第 1 版",
                raw_content="  历史第 1 版原始正文。  ",
                cleaned_content="历史第 1 版原始正文。",
                created_by=database_user.id,
                superseded_by=database_user.id,
            ),
            build_knowledge_document_version(
                version_id="restore-history-2",
                document_id=document.id,
                version=2,
                title="历史第 2 版",
                raw_content="历史第 2 版正文。",
                created_by=database_user.id,
                superseded_by=database_user.id,
            ),
        ]
    )
    database_session.commit()

    provider = MagicMock(
        spec=EmbeddingProvider
    )
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="restore-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=(
            tuple(
                [0.7] * EMBEDDING_DIMENSIONS
            ),
        ),
    )

    restored_document = restore_document_version(
        session=database_session,
        document_id=document.id,
        version=1,
        expected_version=3,
        restored_by=database_user.id,
        embedding_provider=provider,
    )

    assert restored_document is not None
    database_session.expire_all()

    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        document.id,
    )
    saved_versions = database_session.scalars(
        select(
            KnowledgeDocumentVersionRecord
        )
        .where(
            KnowledgeDocumentVersionRecord.document_id
            == document.id
        )
        .order_by(
            KnowledgeDocumentVersionRecord.version.asc()
        )
    ).all()
    saved_chunks = database_session.scalars(
        select(
            KnowledgeChunkRecord
        ).where(
            KnowledgeChunkRecord.document_id
            == document.id
        )
    ).all()

    assert saved_document is not None
    assert saved_document.version == 4
    assert saved_document.title == "历史第 1 版"
    assert saved_document.raw_content == (
        "  历史第 1 版原始正文。  "
    )
    assert saved_document.cleaned_content == (
        "历史第 1 版原始正文。"
    )
    assert saved_document.updated_by == database_user.id
    assert saved_document.embedding_model == (
        "restore-embedding-model"
    )

    # 原来的版本 1、2 保持不变，恢复前的版本 3 新增到历史表。
    assert [
        item.version
        for item in saved_versions
    ] == [
        1,
        2,
        3,
    ]
    assert saved_versions[0].title == "历史第 1 版"
    assert saved_versions[2].title == "当前第 3 版"
    assert saved_versions[2].superseded_by == (
        database_user.id
    )

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id != "current-version-chunk"
    assert saved_chunks[0].content == (
        "历史第 1 版原始正文。"
    )
    provider.embed_texts.assert_called_once()
