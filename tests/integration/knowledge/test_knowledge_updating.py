"""使用真实临时数据库测试知识文档版本更新。"""

from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
    KnowledgeSourceType,
    RawDocument,
    SourcePage,
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
from ticketpilot.knowledge.application.updating import (
    update_document,
    update_document_title,
)


# 验证一次真实更新会同时保存历史、当前版本和新片段。
def test_update_document_persists_history_and_replaces_chunks(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="version-update-document",
        title="旧版密码说明",
        raw_content="员工使用旧流程重置密码。",
    )
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="old-version-chunk",
            content="员工使用旧流程重置密码。",
        )
    )
    database_session.add(document)
    database_session.commit()

    provider = MagicMock(
        spec=EmbeddingProvider
    )
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-update-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=(
            tuple(
                [0.5] * EMBEDDING_DIMENSIONS
            ),
        ),
    )

    updated_document = update_document(
        session=database_session,
        document_id="version-update-document",
        raw_document=RawDocument(
            title="新版密码说明",
            content="员工验证企业邮箱后重置密码。",
        ),
        expected_version=1,
        updated_by=database_user.id,
        embedding_provider=provider,
    )

    assert updated_document is not None
    database_session.expire_all()

    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        "version-update-document",
    )
    saved_history = database_session.scalar(
        select(
            KnowledgeDocumentVersionRecord
        ).where(
            KnowledgeDocumentVersionRecord.document_id
            == "version-update-document"
        )
    )
    saved_chunks = database_session.scalars(
        select(
            KnowledgeChunkRecord
        ).where(
            KnowledgeChunkRecord.document_id
            == "version-update-document"
        )
    ).all()

    assert saved_document is not None
    assert saved_document.title == "新版密码说明"
    assert saved_document.version == 2
    assert saved_document.updated_by == database_user.id
    assert saved_document.embedding_model == "fake-update-model"

    assert saved_history is not None
    assert saved_history.version == 1
    assert saved_history.title == "旧版密码说明"
    assert saved_history.superseded_by == database_user.id

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id != "old-version-chunk"
    assert saved_chunks[0].content == (
        "员工验证企业邮箱后重置密码。"
    )
    # 手动输入的新版正文没有来源页码。
    assert saved_chunks[0].page_numbers == []


# 验证更新 PDF 内容后，新片段仍保存原始页码。
def test_update_document_persists_new_pdf_page_numbers(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="pdf-page-update-document",
        title="旧版 PDF 说明",
        raw_content="旧版说明。",
    )
    document.status = KnowledgeDocumentStatus.READY.value
    document.source_type = KnowledgeSourceType.FILE.value
    document.source_uri = "page-update.pdf"
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="old-pdf-page-chunk",
            content="旧版说明。",
        )
    )
    database_session.add(document)
    database_session.commit()

    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-update-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=(
            tuple([0.5] * EMBEDDING_DIMENSIONS),
        ),
    )
    second_page = "第二页介绍密码重置。"
    fourth_page = "第四页介绍管理员审批。"

    updated_document = update_document(
        session=database_session,
        document_id=document.id,
        raw_document=RawDocument(
            title="新版 PDF 说明",
            content=f"{second_page}\n\n{fourth_page}",
            source_type=KnowledgeSourceType.FILE,
            source_uri="page-update.pdf",
            source_pages=(
                SourcePage(page_number=2, text=second_page),
                SourcePage(page_number=4, text=fourth_page),
            ),
        ),
        expected_version=1,
        updated_by=database_user.id,
        embedding_provider=provider,
    )

    assert updated_document is not None
    database_session.expire_all()
    saved_chunks = database_session.scalars(
        select(KnowledgeChunkRecord).where(
            KnowledgeChunkRecord.document_id == document.id
        )
    ).all()

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id != "old-pdf-page-chunk"
    # 空白页可能被解析器跳过，所以不能按连续页号重编号。
    assert saved_chunks[0].page_numbers == [2, 4]


# 验证只改标题会产生历史版本，但不重写正文或知识片段。
def test_update_document_title_preserves_content_and_chunks(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="title-update-document",
        title="旧标题",
        raw_content="维修部分保修叁个月。",
    )
    document.status = KnowledgeDocumentStatus.READY.value
    document.source_type = "file"
    document.source_uri = "contract.docx"
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="original-title-chunk",
            content="维修部分保修叁个月。",
            embedding=[0.25] * EMBEDDING_DIMENSIONS,
        )
    )
    original_hash = document.content_hash
    database_session.add(document)
    database_session.commit()

    updated_document = update_document_title(
        session=database_session,
        document_id=document.id,
        title="维修合同",
        expected_version=1,
        updated_by=database_user.id,
    )

    assert updated_document is not None
    database_session.expire_all()
    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        "title-update-document",
    )
    saved_history = database_session.scalar(
        select(KnowledgeDocumentVersionRecord).where(
            KnowledgeDocumentVersionRecord.document_id
            == "title-update-document"
        )
    )
    saved_chunks = database_session.scalars(
        select(KnowledgeChunkRecord).where(
            KnowledgeChunkRecord.document_id
            == "title-update-document"
        )
    ).all()

    assert saved_document is not None
    assert saved_document.title == "维修合同"
    assert saved_document.version == 2
    assert saved_document.raw_content == "维修部分保修叁个月。"
    assert saved_document.cleaned_content == "维修部分保修叁个月。"
    assert saved_document.content_hash == original_hash
    assert saved_document.source_type == "file"
    assert saved_document.source_uri == "contract.docx"
    assert saved_document.updated_by == database_user.id

    assert saved_history is not None
    assert saved_history.version == 1
    assert saved_history.title == "旧标题"
    assert saved_history.raw_content == "维修部分保修叁个月。"
    assert saved_history.superseded_by == database_user.id

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id == "original-title-chunk"
    assert saved_chunks[0].content == "维修部分保修叁个月。"
    assert saved_chunks[0].embedding == [0.25] * EMBEDDING_DIMENSIONS
