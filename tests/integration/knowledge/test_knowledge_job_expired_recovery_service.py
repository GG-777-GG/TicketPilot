"""文件职责：验证过期任务恢复 Service 及 Worker 启动恢复的数据库结果。

作用与配合：使用真实恢复逻辑，检查提交、取消收尾、未到期及已有文档保护。
使用位置：知识入库任务恢复功能的集成回归测试。
边界：只使用测试 fixture 的内存 SQLite，不连接开发数据库或模型服务。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from tests.factories.knowledge import build_knowledge_document
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.application import jobs
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import claim_queued_ingestion_job
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)
from ticketpilot.knowledge.workers import worker_cli


# 这个函数负责准备一条已经被 Worker 领取的任务。
def create_running_job(
    session: Session,
    requested_by: str,
    *,
    lease_token: str,
    lease_expires_at: datetime,
) -> str:
    job = jobs.submit_ingestion_job(
        session=session,
        raw_document=RawDocument(
            title="过期任务恢复服务测试",
            content="任务恢复必须经过 Service 提交事务。",
        ),
        requested_by=requested_by,
    )
    assert claim_queued_ingestion_job(
        session,
        job.id,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    ) is not None
    session.commit()
    return job.id


# 这个测试负责确认 Service 恢复任务后会提交 queued 状态。
def test_recover_expired_job_commits_requeue(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    job_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="old-worker-token",
        lease_expires_at=now - timedelta(seconds=1),
    )

    recovered = jobs.recover_expired_ingestion_job(
        session=database_session,
        job_id=job_id,
        now=now,
    )

    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.lease_token is None
    assert recovered.lease_expires_at is None
    assert recovered.attempt_count == 1
    assert database_session.in_transaction() is False
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "queued"


# 这个测试负责确认任务没有到期时 Service 不会恢复它。
def test_recover_unexpired_job_returns_none(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    job_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="current-worker-token",
        lease_expires_at=now + timedelta(seconds=1),
    )

    assert jobs.recover_expired_ingestion_job(
        session=database_session,
        job_id=job_id,
        now=now,
    ) is None
    assert database_session.in_transaction() is False
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.lease_token == "current-worker-token"


# 这个测试负责确认已经有文档的任务不能被 Service 重新排队。
def test_recover_expired_job_with_document_returns_none(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    job_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="old-worker-token",
        lease_expires_at=now - timedelta(seconds=1),
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job_id,
    )
    document.status = "ready"
    database_session.add(document)
    database_session.commit()

    assert jobs.recover_expired_ingestion_job(
        session=database_session,
        job_id=job_id,
        now=now,
    ) is None
    assert database_session.in_transaction() is False
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "running"


# 这个测试负责确认批量恢复只返回真正重新排队的任务 ID。
def test_recover_expired_jobs_skips_existing_document_and_unexpired_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    recoverable_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="recoverable-token",
        lease_expires_at=now - timedelta(minutes=3),
    )
    existing_document_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="document-token",
        lease_expires_at=now - timedelta(minutes=2),
    )
    unexpired_id = create_running_job(
        database_session,
        database_user.id,
        lease_token="unexpired-token",
        lease_expires_at=now + timedelta(minutes=1),
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=existing_document_id,
    )
    document.status = "ready"
    database_session.add(document)
    database_session.commit()

    recovered_ids = jobs.recover_expired_ingestion_jobs(
        session=database_session,
        now=now,
        limit=10,
    )

    assert recovered_ids == (recoverable_id,)
    recovered = database_session.get(KnowledgeIngestionJobRecord, recoverable_id)
    with_document = database_session.get(
        KnowledgeIngestionJobRecord,
        existing_document_id,
    )
    unexpired = database_session.get(KnowledgeIngestionJobRecord, unexpired_id)
    assert recovered is not None and recovered.status == "queued"
    assert with_document is not None and with_document.status == "running"
    assert unexpired is not None and unexpired.status == "running"


def test_worker_startup_recovers_expired_cancel_and_preserves_guards(
    # fixture 提供的内存数据库会话，用于准备和核对测试数据。
    database_session: Session,
    # 内存数据库中已创建的任务提交人。
    database_user: UserRecord,
    # 将 Worker 资源绑定到测试数据库，并替换模型和后续领取。
    monkeypatch,
) -> None:
    """启动时完成过期取消、重排过期运行，保留未到期或已有文档的取消任务。"""
    reference_time = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    job_ids: dict[str, str] = {}

    # 同时构造可以恢复和必须保留的情况，避免只验证正常分支。
    for name, expired in (
        ("cancel", True),
        ("running", True),
        ("future", False),
        ("document", True),
    ):
        job_ids[name] = create_running_job(
            database_session,
            database_user.id,
            lease_token=f"startup-token-{name}",
            lease_expires_at=(
                reference_time + timedelta(minutes=-1 if expired else 30)
            ),
        )

        if name != "running":
            cancelled = jobs.cancel_ingestion_job(
                session=database_session,
                job_id=job_ids[name],
            )
            assert cancelled is not None and cancelled.status == "cancelling"

    # 已有同 ID 文档的取消任务必须受保护，不能直接标记取消完成。
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job_ids["document"],
    )
    document.status = "ready"
    database_session.add(document)
    database_session.commit()

    class FrozenDateTime(datetime):
        """固定启动恢复的时间，避免测试依赖机器当前时间。"""

        @classmethod
        def now(
            # Python 自动传入的当前类。
            cls,
            # Worker 应明确要求 UTC 时区。
            tz=None,
        ) -> datetime:
            assert tz is timezone.utc
            return reference_time

    # 创建独立 Session，复用同一个内存数据库；不关闭 fixture 的会话。
    test_session_factory = sessionmaker(
        bind=database_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(worker_cli, "SessionLocal", test_session_factory)
    monkeypatch.setattr(worker_cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)

    def stop_before_claim(
        *,
        # Worker 首次领取时新建的处理会话。
        session: Session,
        # 测试替换的模型占位对象，不会发出请求。
        embedding_provider: object,
    ) -> None:
        """在实际领取之前结束循环，避免重新排队的任务被测试消费。"""
        assert session is not database_session
        assert embedding_provider is not None
        raise KeyboardInterrupt

    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", stop_before_claim)
    worker_cli.watch_queued_jobs()

    # 恢复使用独立会话提交，核对前清除 fixture 会话中的旧 ORM 缓存。
    database_session.expire_all()
    saved = {
        name: database_session.get(KnowledgeIngestionJobRecord, job_id)
        for name, job_id in job_ids.items()
    }
    assert all(record is not None for record in saved.values())
    assert saved["cancel"].status == "cancelled"
    assert saved["cancel"].lease_token is None
    assert saved["cancel"].lease_expires_at is None
    assert saved["cancel"].document_id is None
    assert saved["running"].status == "queued"
    assert saved["future"].status == "cancelling"
    assert saved["future"].lease_token == "startup-token-future"
    assert saved["document"].status == "cancelling"
    assert saved["document"].lease_token == "startup-token-document"
    assert all(record.attempt_count == 1 for record in saved.values())

    saved_document = database_session.get(KnowledgeDocumentRecord, job_ids["document"])
    assert saved_document is not None and saved_document.status == "ready"
