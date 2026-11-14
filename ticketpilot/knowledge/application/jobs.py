"""管理知识入库任务。

提交流程：
接收原始文档和提交人
→ 创建 queued 任务
→ Repository 写入任务
→ 提交事务，让后续 Worker 能读取
"""

from typing import Literal
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.knowledge.domain.documents import (
    RawDocument,
    KnowledgeIngestionJobStatus,
)

from ticketpilot.knowledge.persistence.job_repository import (
    KnowledgeJobRepositoryError,
    cancel_queued_ingestion_job,
    add_ingestion_job,
    claim_queued_ingestion_job,
    get_ingestion_job,
    requeue_expired_ingestion_job,
    requeue_failed_ingestion_job,
    find_expired_ingestion_job_ids,
    request_running_ingestion_job_cancellation,
    mark_ingestion_job_cancelled,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord
from datetime import datetime, timedelta, timezone

# 上传检查：验证文件名、大小和支持的类型。
from ticketpilot.knowledge.application.uploads import (
    KnowledgeFileUploadService,
    KnowledgeFileValidationError,
)

# 原始文件的保存与失败清理。
from ticketpilot.knowledge.persistence.file_storage import (
    LocalKnowledgeFileStorage,
    KnowledgeFileStorageError,
)

JOB_LEASE_DURATION = timedelta(minutes=30)

class KnowledgeJobSubmissionError(RuntimeError):
    """表示提交入库任务失败。"""


# 这个函数负责创建并持久保存一条待处理任务。
def submit_ingestion_job(
    *,
    session: Session,
    raw_document: RawDocument,
    requested_by: str,
    visibility: Literal["internal", "restricted"] = "internal",
) -> KnowledgeIngestionJobRecord:
    job = KnowledgeIngestionJobRecord(
        id=str(uuid4()),
        title=raw_document.title,
        raw_content=raw_document.content,
        source_type=raw_document.source_type.value,
        source_uri=raw_document.source_uri,
        visibility=visibility,
        requested_by=requested_by,
    )

    try:
        saved_job = add_ingestion_job(session, job)
        session.commit()
    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        session.rollback()
        raise KnowledgeJobSubmissionError(
            "提交知识入库任务失败"
        ) from error

    return saved_job


def _build_file_ingestion_job(
    *,
    # 文档标题，例如“设备维修合同”。
    # 调用前应已完成标题检查。
    title: str,

    # 原始文件名，例如“维修合同.pdf”。
    # 已通过上传检查，用于后续选择解析器和记录来源。
    original_filename: str,

    # 文件保存成功后，由 storage.save() 返回的内部标识。
    # 不能直接采用用户请求提供的值。
    storage_key: str,

    # 提交任务的用户 ID，应来自已登录用户。
    requested_by: str,

    # 文档可见范围，由上层校验权限后传入。
    visibility: Literal["internal", "restricted"],
) -> KnowledgeIngestionJobRecord:
    """构造文件入库任务，不执行文件操作或数据库提交。"""

    return KnowledgeIngestionJobRecord(
        # 任务 ID 与磁盘文件标识是两个不同的标识。
        id=str(uuid4()),

        # 保存后续生成知识文档所需的标题。
        title=title,

        # 此时文件尚未解析，没有正文。
        # 与数据库的“文件输入”约束保持一致。
        raw_content=None,

        # Worker 根据这个标识读取原始 bytes。
        storage_key=storage_key,

        # Worker 根据原始文件名选择 PDF、DOCX 等解析器。
        original_filename=original_filename,

        # 此方法只构造文件任务，来源固定为 file。
        source_type="file",

        # 保持项目已有约定：文件来源记录原始文件名，
        # 不对外暴露服务器磁盘路径。
        source_uri=original_filename,

        # 保留提交时经过权限检查的可见范围。
        visibility=visibility,

        # 记录任务由谁提交。
        requested_by=requested_by,
    )

# 提交文件入库任务：检查上传信息、保存原始文件，
# 将文件标识和提交人等信息写入数据库，创建等待 Worker 处理的任务。
# 数据库写入失败时清理文件；提交结果不确定时保留文件，供后续核实。
def submit_file_ingestion_job(
    *,
    # 本次提交任务使用的数据库会话。
    session: Session,

    # 原始文件存储服务；测试时可以使用临时目录。
    storage: LocalKnowledgeFileStorage,

    # 上传检查服务；这里不会执行解析或 OCR。
    upload_service: KnowledgeFileUploadService,

    # 用户为知识文档填写的标题。
    title: str,

    # 上传时的原始文件名，例如“维修合同.pdf”。
    filename: str,

    # 请求声明的文件类型，可能为空。
    content_type: str | None,

    # 上传文件的原始二进制内容。
    content: bytes,

    # 当前登录用户的 ID，由调用方提供。
    requested_by: str,

    # 文档可见范围；调用方还需要检查用户是否有提交权限。
    visibility: Literal["internal", "restricted"],
) -> KnowledgeIngestionJobRecord:
    """检查并保存原始文件，提交一条等待 Worker 处理的任务。"""

    # 先检查标题，避免保存文件后才发现输入无效。
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 200:
        raise KnowledgeFileValidationError(
            "标题不能为空，且不能超过 200 个字符"
        )

    # Literal 只是类型提示，因此仍要做运行时检查。
    if visibility not in ("internal", "restricted"):
        raise KnowledgeFileValidationError(
            "文档可见范围不合法"
        )

    # 先检查文件名、大小和支持的类型。
    # 检查失败时，还没有保存文件，也没有创建任务。
    upload_service.validate_upload(
        filename=filename,
        content_type=content_type,
        content=content,
    )

    # 保存原始文件，取得内部文件标识。
    storage_key = storage.save(content=content)

    # 用刚才检查过的信息构造文件任务。
    job = _build_file_ingestion_job(
        title=normalized_title,
        original_filename=filename,
        storage_key=storage_key,
        requested_by=requested_by,
        visibility=visibility,
    )

    try:
        # Repository 执行 add 和 flush，但这里尚未 commit。
        saved_job = add_ingestion_job(session, job)

    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        # 尚未提交，先撤销数据库事务。
        session.rollback()

        try:
            # 回收本次保存、但没有成功提交任务的文件。
            storage.delete(storage_key=storage_key)
        except KnowledgeFileStorageError as cleanup_error:
            raise KnowledgeJobSubmissionError(
                "文件任务写入失败，且原始文件清理失败"
            ) from cleanup_error

        raise KnowledgeJobSubmissionError(
            "提交文件入库任务失败"
        ) from error

    # 在提交前保存 ID，便于提交异常时定位任务。
    job_id = job.id

    try:
        # 提交后，其他 Worker 才能读取并领取这条任务。
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()

        # commit 期间连接中断时，数据库可能已经提交成功。
        # 此时保留文件，避免任务存在却失去输入文件。
        raise KnowledgeJobSubmissionError(
            f"文件任务提交状态待核实，任务 ID：{job_id}；"
            "原始文件已保留"
        ) from error

    return saved_job


class KnowledgeJobStartError(RuntimeError):
    """表示启动知识入库任务失败。"""


# 这个函数负责领取任务并提交 running 状态。
def start_ingestion_job(
    *,
    session: Session,
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    try:
        # 每次尝试领取都生成新的内部标识，不是用户的 JWT。
        lease_token = str(uuid4())
        lease_expires_at = datetime.now(timezone.utc) + JOB_LEASE_DURATION

        job = claim_queued_ingestion_job(
            session,
            job_id,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
        )

        if job is None:
            session.rollback()
            return None

        session.commit()
        return job
    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        session.rollback()
        raise KnowledgeJobStartError(
            "启动知识入库任务失败"
        ) from error

class KnowledgeJobRetryError(RuntimeError):
    """表示重新排队失败任务时出错。"""


# 这个函数负责提交“失败任务重新排队”的状态变化。
def retry_ingestion_job(
    *,
    session: Session,
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    try:
        job = requeue_failed_ingestion_job(session, job_id)

        if job is None:
            session.rollback()
            return None

        session.commit()
        return job
    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        session.rollback()
        raise KnowledgeJobRetryError(
            "重试知识入库任务失败"
        ) from error

class KnowledgeJobRecoveryError(RuntimeError):
    """表示恢复租约过期的入库任务时出错。"""


# 恢复流程：
# 查询任务并取得当前租约 token
# → Repository 原子地检查是否允许重新排队
# → 成功则提交；不符合条件则回滚
def recover_expired_ingestion_job(
    *,
    session: Session,
    job_id: str,
    now: datetime,
) -> KnowledgeIngestionJobRecord | None:
    """恢复一条租约已过期、尚未生成文档的任务。"""
    try:
        job = get_ingestion_job(session, job_id)

        if job is None or job.lease_token is None:
            session.rollback()
            return None

        # 先记下当前 token；Repository 更新时会再次核对它。
        lease_token = job.lease_token
        # 正在取消的过期任务，优先完成取消收尾。
        # expired_before 保证恢复流程只处理已经到期的租约。
        cancelled = mark_ingestion_job_cancelled(
            session,
            job_id,
            lease_token=lease_token,
            expired_before=now,
        )

        if cancelled is not None:
            session.commit()
            return cancelled
        recovered = requeue_expired_ingestion_job(
            session,
            job_id,
            lease_token=lease_token,
            now=now,
        )

        if recovered is None:
            session.rollback()
            return None

        session.commit()
        return recovered

    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        session.rollback()
        raise KnowledgeJobRecoveryError(
            "恢复租约过期的知识入库任务失败"
        ) from error

# 批量恢复流程：
# 查询租约已过期的任务 ID
# → 逐条调用单条恢复函数
# → 只返回真正恢复成功的任务 ID
def recover_expired_ingestion_jobs(
    *,
    session: Session,
    now: datetime,
    limit: int = 100,
) -> tuple[str, ...]:
    """批量恢复租约过期且尚未生成文档的任务。"""
    try:
        job_ids = find_expired_ingestion_job_ids(
            session,
            now=now,
            limit=limit,
        )
    except (KnowledgeJobRepositoryError, SQLAlchemyError) as error:
        session.rollback()
        raise KnowledgeJobRecoveryError(
            "查询待恢复的知识入库任务失败"
        ) from error

    if not job_ids:
        session.rollback()
        return ()

    recovered_ids: list[str] = []

    for job_id in job_ids:
        recovered = recover_expired_ingestion_job(
            session=session,
            job_id=job_id,
            now=now,
        )
        if recovered is not None:
            recovered_ids.append(recovered.id)

    return tuple(recovered_ids)


class KnowledgeJobCancellationError(RuntimeError):
    """表示取消入库任务时发生数据库等内部错误。"""


def cancel_ingestion_job(
    *,
    # 当前请求的数据库会话。
    session: Session,

    # 已由 Router 检查管理权限的任务 ID。
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    """取消排队任务，或为运行中的任务登记取消申请。

    queued 改为 cancelled，running 改为 cancelling。
    重复请求返回当前取消状态，不重复创建任务。
    其他状态返回 None；数据库错误时回滚并转换异常。
    不直接中断 OCR 或模型调用，不删除原始文件。
    """

    try:
        # 优先尝试取消等待中的任务。
        job = cancel_queued_ingestion_job(session, job_id)

        if job is None:
            # 已经开始处理时，尝试登记取消申请。
            job = request_running_ingestion_job_cancellation(
                session,
                job_id,
            )

        if job is None:
            # 两次更新都没有匹配，可能已经有人申请或完成取消。
            # 先结束旧事务，再读取最新状态。
            session.rollback()

            job = get_ingestion_job(
                session,
                job_id,
                fresh=True,
            )

            if job is None or job.status not in (
                KnowledgeIngestionJobStatus.CANCELLING.value,
                KnowledgeIngestionJobStatus.CANCELLED.value,
            ):
                session.rollback()
                return None

        # 重复请求的读取分支也结束事务，保留可读取的返回对象。
        session.commit()
        return job

    except (
        KnowledgeJobRepositoryError,
        SQLAlchemyError,
    ) as error:
        session.rollback()

        raise KnowledgeJobCancellationError(
            "取消知识入库任务失败"
        ) from error