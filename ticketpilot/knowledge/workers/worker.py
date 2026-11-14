"""处理持久化的知识入库任务。

业务流程：
从数据库任务恢复原始文档
→ 后续交给入库流水线处理
→ 记录任务成功或失败
"""
from sqlalchemy.orm import Session

# Engine 管理数据库连接；用于检查进度会话的数据库绑定。
from sqlalchemy import Engine

# 捕获进度保存时发生的数据库异常。
from sqlalchemy.exc import SQLAlchemyError

from ticketpilot.knowledge.embeddings.base import EmbeddingProvider
from ticketpilot.knowledge.application.ingestion import (
    KnowledgeIngestionError,
    ingest_document,
)
from ticketpilot.knowledge.persistence.job_repository import (
    mark_ingestion_job_failed,
    mark_ingestion_job_succeeded,
    find_next_queued_ingestion_job_id,
    # 带任务状态和租约检查的阶段更新方法。
    update_ingestion_job_stage,
    KnowledgeJobRepositoryError,
    mark_ingestion_job_cancelled,
    get_ingestion_job,
)
from ticketpilot.knowledge.application.jobs import start_ingestion_job

from ticketpilot.knowledge.domain.documents import (
    KnowledgeIngestionJobStatus,
    KnowledgeSourceType,
    RawDocument,
    # 保存原始页码和每页文字，供后续来源溯源使用。
    SourcePage,
    KnowledgeProcessingStage,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord

# 检查文件并调用解析器；PDF 扫描页会在这里触发 OCR。
from ticketpilot.knowledge.application.uploads import (
    KnowledgeFileUploadService,
    KnowledgeFileValidationError,
)
# 读取服务端配置的原始文件存储目录。
from ticketpilot.core.config import settings

from ticketpilot.knowledge.persistence.file_storage import (
    LocalKnowledgeFileStorage,

    # 文件丢失、读取权限不足等存储错误。
    KnowledgeFileStorageError,
)

# PDF 损坏、OCR 失败等解析错误。
from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParsingError,
)

# 找不到支持该文件的解析器。
from ticketpilot.knowledge.parsers.registry import (
    UnsupportedKnowledgeFileTypeError,
)


# 这个函数负责从数据库任务恢复入库所需的原始文档。
def build_raw_document_from_job(
    # Worker 已领取的数据库任务。
    job: KnowledgeIngestionJobRecord,
    *,
    # 可传入指定存储服务，方便测试使用临时目录。
    # 不传时，文件任务使用配置中的默认目录。
    storage: LocalKnowledgeFileStorage | None = None,

    # 可传入指定解析服务，方便测试替换 PDF/OCR。
    # 不传时，文件任务创建默认上传解析服务。
    upload_service: KnowledgeFileUploadService | None = None,
) -> RawDocument:
    """根据任务输入，恢复知识入库所需的原始文档。

    正常流程：
        有 storage_key：读取原始文件，再执行解析或 OCR。
        没有 storage_key：直接使用任务中已有的正文。
        两种分支最终都返回 RawDocument。

    异常处理：
        正文任务没有正文时，抛出输入校验异常。
        文件读取、检查或解析失败时，异常继续向上传递，
        交给 Worker 主流程记录任务失败。

    返回：
        可以交给清洗、分块和向量生成流程的 RawDocument。

    调用约定：
        本函数不修改任务、不提交数据库事务。
        文件任务使用的目录必须与提交任务时一致。
    """

    # 用文件标识判断任务是否需要读取原始文件。
    # 不能只判断 source_type，因为旧任务可能已经保存了文件正文。
    if job.storage_key is not None:
        # 显式传入时使用传入对象；否则使用项目配置创建。
        file_storage = (
            storage
            if storage is not None
            else LocalKnowledgeFileStorage(
                directory=settings.knowledge_upload_directory,
            )
        )

        file_upload_service = (
            upload_service
            if upload_service is not None
            else KnowledgeFileUploadService()
        )

        # 委托刚才添加的方法，完成文件读取和解析。
        return _build_raw_document_from_file_job(
            job=job,
            storage=file_storage,
            upload_service=file_upload_service,
        )

    # 没有待解析文件，就必须有已经保存的正文。
    if job.raw_content is None:
        raise KnowledgeFileValidationError(
            "入库任务缺少正文或原始文件"
        )

    # 正文输入沿用现有处理方式。
    return RawDocument(
        title=job.title,
        content=job.raw_content,
        source_type=KnowledgeSourceType(job.source_type),
        source_uri=job.source_uri,
    )

def _build_raw_document_from_file_job(
    *,
    # 数据库中保存的文件任务。
    job: KnowledgeIngestionJobRecord,

    # 原始文件存储服务，负责根据内部标识读取 bytes。
    storage: LocalKnowledgeFileStorage,

    # 文件解析服务，负责选择 TXT、PDF、DOCX 等解析器。
    upload_service: KnowledgeFileUploadService,
) -> RawDocument:
    """读取并解析文件任务，构造知识入库所需的原始文档。

    正常流程：
        检查文件任务信息
        → 根据 storage_key 读取原始 bytes
        → 根据原始文件名选择解析器
        → 提取文字，扫描 PDF 必要时执行 OCR
        → 保留页码信息，返回 RawDocument

    异常处理：
        文件任务信息不完整时，抛出上传校验异常。
        文件读取或解析失败时，异常继续向上传递，
        由后续 Worker 处理流程统一记录任务失败。

    返回：
        携带标题、正文、文件来源和可用页码的 RawDocument。

    调用约定：
        API 与 Worker 必须能够访问同一个文件存储目录。
        本函数不修改任务记录，也不提交数据库事务。
    """

    # 即使数据库已有约束，这里仍明确检查方法需要的输入。
    # 也避免把 None 传给文件读取或解析方法。
    if (
        job.source_type != "file"
        or not job.storage_key
        or not job.original_filename
    ):
        raise KnowledgeFileValidationError(
            "文件任务信息不完整或来源类型错误"
        )

    # 文件读取只使用内部标识，不能使用原始文件名拼接路径。
    content = storage.read(
        storage_key=job.storage_key,
    )

    # 到 Worker 执行时，才真正解析文件。
    parsed_file = upload_service.parse_upload(
        # 用原始名称识别扩展名，例如 .pdf、.docx。
        filename=job.original_filename,

        # 当前任务没有保存上传请求的 MIME 类型。
        # 现有解析器可以根据扩展名选择，并检查实际内容。
        content_type=None,

        # 刚从磁盘读取的原始二进制内容。
        content=content,
    )

    return RawDocument(
        # 使用提交任务时保存的文档标题。
        title=job.title,

        # 解析器提取的整篇文字。
        content=parsed_file.text,

        # 文档来源明确为文件。
        source_type=KnowledgeSourceType.FILE,

        # 保留原始文件名作为来源信息。
        source_uri=job.original_filename,

        # 把解析器的页面对象转换成业务层的页面对象。
        # 没有页码的文件，转换结果就是空元组。
        source_pages=tuple(
            SourcePage(
                page_number=page.page_number,
                text=page.text,
            )
            for page in parsed_file.pages
        ),
    )

class KnowledgeJobProcessingError(RuntimeError):
    """表示任务状态无法正确完成转换。"""

class KnowledgeJobLeaseLostError(KnowledgeJobProcessingError):
    """表示本次 Worker 已不再持有任务，应停止处理。"""

# 这个函数负责处理一条待入库任务，并记录成功或失败。
def process_ingestion_job(
    *,
    # 当前任务处理使用的数据库会话，由本函数控制事务。
    session: Session,
    # 要尝试领取并处理的任务 ID。
    job_id: str,
    # 为清洗、分块后的文字生成向量，测试可传入模拟实现。
    embedding_provider: EmbeddingProvider,
) -> KnowledgeIngestionJobRecord | None:
    """领取并处理一条知识入库任务。

    正常流程：
        原子领取任务并提交 running 状态
        → 取得已有正文，或读取文件并解析、执行 OCR
        → 完成清洗、分块、向量生成和文档保存
        → 使用领取时的租约 token 标记成功并统一提交。

    异常处理：
        文件读取、校验、解析或入库失败时，回滚处理事务，
        再单独提交 failed 状态；状态或租约变化时抛出处理异常。

    返回：
        处理后的任务记录；任务不存在或无法领取时返回 None。

    调用约定：
        本函数负责提交和回滚；失败时保留原始文件供重试。
        Worker 使用的文件目录必须与上传端一致。
    """
    # 先领取并提交 running 状态。
    job = start_ingestion_job(session=session, job_id=job_id)
    if job is None:
        return None
    # 保存本次领取的固定信息，后续不重新获取可能变化的租约。
    lease_token = job.lease_token
    created_by = job.requested_by
    visibility = job.visibility

    if lease_token is None:
        session.rollback()
        raise KnowledgeJobProcessingError(
            "领取任务后缺少租约标识"
        )

    # 使用当前任务所在的数据库，测试时仍访问测试数据库。
    database_bind = session.get_bind()

    def finish_cancellation() -> tuple[
        KnowledgeIngestionJobRecord | None,
        bool,
    ]:
        """回滚主事务后，确认取消并判断任务归属。

        返回两个值：
        1. 已取消的任务，或者 None。
        2. 当前 Worker 是否仍持有本次未结束任务。
        """

        if not isinstance(database_bind, Engine):
            raise KnowledgeJobProcessingError(
                "确认取消需要绑定 Engine 的 Session"
            )

        try:
            # 新会话读取真实状态，避免使用旧 ORM 缓存。
            with Session(
                bind=database_bind,
                autoflush=False,
                expire_on_commit=False,
            ) as control_session:
                cancelled = mark_ingestion_job_cancelled(
                    control_session,
                    job_id,
                    lease_token=lease_token,
                )

                if cancelled is not None:
                    control_session.commit()
                    return cancelled, False

                # 可能已经被租约恢复流程取消。
                latest = get_ingestion_job(
                    control_session,
                    job_id,
                    fresh=True,
                )

                if (
                    latest is not None
                    and latest.status
                    == KnowledgeIngestionJobStatus.CANCELLED.value
                ):
                    control_session.commit()
                    return latest, False

                # 没有完成取消时，判断任务是否仍属于本次领取。
                still_owned = (
                    latest is not None
                    and latest.lease_token == lease_token
                    and latest.status in (
                        KnowledgeIngestionJobStatus.RUNNING.value,
                        KnowledgeIngestionJobStatus.CANCELLING.value,
                    )
                )

                control_session.commit()
                return None, still_owned

        except (
            KnowledgeJobRepositoryError,
            SQLAlchemyError,
        ) as error:
            raise KnowledgeJobProcessingError(
                "确认知识入库任务取消失败"
            ) from error

    def report_stage(
            # 流水线即将执行的阶段。
            stage: KnowledgeProcessingStage,
    ) -> None:
        """单独提交当前阶段，让页面能够及时查询。

        本回调只允许在文档开始写入数据库之前调用。
        """
        # 避免新 Session 意外共用调用方的外部事务。
        # 当前 Worker 和测试工厂都使用 Engine 绑定。
        if not isinstance(database_bind, Engine):
            raise KnowledgeJobProcessingError(
                "阶段上报需要绑定 Engine 的 Session"
            )

        try:
            # 使用独立 Session 保存进度，
            # 不在保存文档的主 Session 上提前 commit。
            with Session(
                    bind=database_bind,
                    autoflush=False,
                    expire_on_commit=False,
            ) as progress_session:
                updated = update_ingestion_job_stage(
                    progress_session,
                    job_id,
                    stage=stage,
                    lease_token=lease_token,
                )

                if not updated:
                    # 任务状态或租约已变化，停止当前处理。
                    raise KnowledgeJobLeaseLostError(
                        "任务状态或租约已变化，停止本次处理"
                    )

                # 这里只提交任务阶段，不包含文档和片段。
                progress_session.commit()

        except (
                KnowledgeJobRepositoryError,
                SQLAlchemyError,
        ) as error:
            raise KnowledgeJobProcessingError(
                "记录知识入库任务阶段失败"
            ) from error

    try:
        # 有待读取的原始文件，才需要报告解析阶段。
        # 扫描 PDF 的 OCR 也包含在当前解析过程中。
        if job.storage_key is not None:
            report_stage(KnowledgeProcessingStage.PARSING)

        raw_document = build_raw_document_from_job(job)

        document = ingest_document(
            session=session,
            raw_document=raw_document,
            created_by=created_by,
            embedding_provider=embedding_provider,
            visibility=visibility,
            document_id=job_id,
            commit=False,

            # 传入函数本身，进入阶段时再调用。
            # 这里不能写 report_stage()。
            on_stage=report_stage,
        )


    except KnowledgeJobLeaseLostError:

        # 必须先撤销文档事务，再确认取消。

        session.rollback()

        cancelled, still_owned = finish_cancellation()

        if cancelled is not None:
            return cancelled

        if still_owned:
            # 任务仍属于自己却无法更新，属于异常情况。

            raise KnowledgeJobProcessingError(

                "无法更新任务状态或确认取消"

            )

        # 已被其他 Worker 接手，正常停止本次处理。

        return None


    except KnowledgeJobProcessingError:

        session.rollback()

        # 第二个返回值这里不用，以 _ 接收。

        cancelled, _ = finish_cancellation()

        if cancelled is not None:
            return cancelled

        # 没有取消申请时，继续抛出原来的系统异常。

        raise

    except (
        KnowledgeIngestionError,
        KnowledgeFileStorageError,
        KnowledgeFileValidationError,
        KnowledgeFileParsingError,
        UnsupportedKnowledgeFileTypeError,
    ) as error:
        # 先撤销本次处理事务，再单独记录失败状态。
        # 如果入库服务已经回滚，再执行一次 rollback 也是允许的。
        session.rollback()

        # 保存简明的失败阶段，便于查询任务时定位问题。
        # 不直接保存底层异常全文，避免暴露磁盘路径等内部信息。
        if isinstance(error, KnowledgeFileStorageError):
            error_message = "原始文件读取失败"
        elif isinstance(error, KnowledgeFileParsingError):
            error_message = "知识文件解析或 OCR 失败"
        elif isinstance(error, UnsupportedKnowledgeFileTypeError):
            error_message = "知识文件类型不受支持"
        elif isinstance(error, KnowledgeFileValidationError):
            error_message = "入库任务输入或文件校验失败"
        else:
            error_message = "知识文档入库失败"

        # 仅允许仍持有本次租约的 Worker 将任务标记为失败。
        failed = mark_ingestion_job_failed(
            session,
            job_id,
            error_message,
            lease_token=lease_token,
        )
        if failed is None:
            session.rollback()

            cancelled, still_owned = finish_cancellation()

            if cancelled is not None:
                return cancelled

            if not still_owned:
                return None

            raise KnowledgeJobProcessingError(
                "处理失败后无法更新任务状态"
            ) from error

        # 失败状态持久化后立即返回，不进入下面的成功分支。
        session.commit()
        return failed

    # 文档已入库，再把任务关联到该文档。
    completed = mark_ingestion_job_succeeded(
        session,
        job_id,
        document.id,
        lease_token=lease_token
    )
    if completed is None:
        # 即使文档已经 flush，也要先回滚，不能留下入库结果。
        session.rollback()

        cancelled, still_owned = finish_cancellation()

        if cancelled is not None:
            return cancelled

        if not still_owned:
            return None

        raise KnowledgeJobProcessingError(
            "文档已入库，但无法更新任务状态"
        )

    session.commit()
    return completed

# 这个函数负责自动找到并处理一条排队中的入库任务。
def process_next_ingestion_job(
    *,
    session: Session,
    embedding_provider: EmbeddingProvider,
) -> KnowledgeIngestionJobRecord | None:
    # 先找候选任务；这一步只是读取，不会领取。
    job_id = find_next_queued_ingestion_job_id(session)
    if job_id is None:
        return None

    # 原有函数会原子领取，再执行入库。
    return process_ingestion_job(
        session=session,
        job_id=job_id,
        embedding_provider=embedding_provider,
    )
