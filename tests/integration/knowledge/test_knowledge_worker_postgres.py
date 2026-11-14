"""使用专用 PostgreSQL 测试库验证任务入库的真实数据库路径。"""

from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord
from ticketpilot.knowledge.workers.worker import process_ingestion_job


# 这个测试负责确认 PostgreSQL 和 pgvector 能保存 Worker 生成的文档。
def test_worker_persists_document_in_postgres(
    postgres_session: Session,
) -> None:
    user = build_user()
    postgres_session.add(user)
    postgres_session.flush()

    job = submit_ingestion_job(
        session=postgres_session,
        raw_document=RawDocument(
            title="后台任务测试",
            content="员工忘记密码后，应联系 IT 服务台处理。",
        ),
        requested_by=user.id,
        visibility="internal",
    )
    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )

    result = process_ingestion_job(
        session=postgres_session,
        job_id=job.id,
        embedding_provider=provider,
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.document_id == job.id
    document = postgres_session.get(KnowledgeDocumentRecord, job.id)
    assert document is not None
    assert document.title == "后台任务测试"
