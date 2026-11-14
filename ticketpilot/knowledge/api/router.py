"""
定义知识库模块的 HTTP 接口。

当前业务流程：
POST /v1/knowledge/documents
→ Pydantic 验证标题和正文
→ 验证 JWT
→ 验证用户是 supervisor 或 admin
→ FastAPI 提供数据库 Session
→ FastAPI 提供 Embedding Provider
→ ingest_document() 执行完整入库
→ 返回文档元数据

职责边界：
- Router 只处理 HTTP 请求、权限、状态码和响应。
- Ingestion Service 负责编排知识入库流程。
- Repository 负责数据库写入。
- Embedding Provider 负责生成向量。
"""

# 导入 FastAPI 路由、HTTP 异常和状态码。
from fastapi import (
    APIRouter,
    # File 表示参数来自 multipart 文件。
    File,
    # Form 表示参数来自 multipart 普通表单字段。
    Form,
    HTTPException,
    # Path 用来验证 URL 路径参数。
    Path,
    Query,
    status,
    # Response 用来返回没有响应正文的 HTTP 204。
    Response,
    # UploadFile 表示客户端上传的文件。
    UploadFile,
)
# Repository 负责查询知识文档列表。
from ticketpilot.knowledge.persistence import (
    repository as knowledge_repository,
)

# Annotated 用来组合参数类型和 FastAPI 查询规则。
from typing import Annotated, Literal

# 导入请求独享的数据库 Session 依赖。
from ticketpilot.core.dependencies import (
    DatabaseSession,
)

# 根据已登录用户的角色，确定可检索的文档类型。
from ticketpilot.knowledge.domain.access import (
    allowed_visibilities_for_role,
)

# 只有主管或管理员才能修改企业知识库。
from ticketpilot.identity.dependencies import (
    SupervisorOrAdminUser,
    #登录后都可以检索知识
    CurrentUser,
)

# 导入知识库使用的向量模型依赖。
from ticketpilot.knowledge.api.dependencies import (
    EmbeddingProviderDependency,
    # FastAPI 自动提供文件上传 Service。
    KnowledgeFileUploadServiceDependency,
    # FastAPI 自动提供原始文件存储对象。
    KnowledgeFileStorageDependency,
)

# 导入入库流水线内部使用的原始文档。
from ticketpilot.knowledge.domain.documents import (
    # 表示上传文档的来源类型是 file。
    KnowledgeSourceType,
    RawDocument,
    SourcePage,  # 业务层使用的“页码 + 文字”对象。
    # 入库任务的四种合法状态。
    KnowledgeIngestionJobStatus,
)

from ticketpilot.knowledge.parsers import (
    # 文件内容不能按规定格式解析。
    KnowledgeFileParsingError,

    # 系统不支持该文件扩展名。
    UnsupportedKnowledgeFileTypeError,
)

from ticketpilot.knowledge.application.uploads import (
    # 上传文件超过大小限制。
    KnowledgeFileTooLargeError,

    # 文件名或文件内容没有通过验证。
    KnowledgeFileValidationError,
)

# 导入完整入库服务和统一异常。
from ticketpilot.knowledge.application.ingestion import (
    KnowledgeIngestionError,
    ingest_document,
)

# 导入知识文档管理服务和统一异常。
from ticketpilot.knowledge.application.management import (
    # 表示文档当前状态不允许执行操作。
    InvalidKnowledgeDocumentStateError,

    # 表示数据库等内部管理操作失败。
    KnowledgeManagementError,

    # 归档知识文档。
    archive_document,

    # 恢复已经归档的知识文档。
    restore_document,
)

# 导入知识文档更新服务和可能发生的业务异常。
from ticketpilot.knowledge.application.updating import (
    # 文档当前状态不允许更新。
    InvalidKnowledgeDocumentUpdateStateError,

    # 用户提交的内容与当前文档完全相同。
    KnowledgeDocumentNoChangesError,

    # 更新过程发生数据库或向量模型异常。
    KnowledgeDocumentUpdateError,

    # 客户端提交的版本号已经过期。
    KnowledgeDocumentVersionConflictError,

    # 执行完整的知识文档更新流程。
    update_document,

    update_document_title,
)

# 导入知识文档 ORM 类型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
)

# 导入 HTTP 请求和响应 Schema。
from ticketpilot.knowledge.api.schemas import (
    # 更新知识文档时的请求数据。
    KnowledgeDocumentUpdateRequest,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDetailResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResultResponse,
    # 历史版本分页响应。
    KnowledgeDocumentVersionListResponse,
    # 单条历史版本摘要响应。
    KnowledgeDocumentVersionResponse,
    # 单个历史版本的完整详情响应。
    KnowledgeDocumentVersionDetailResponse,
    # 恢复历史版本时的请求数据。
    KnowledgeDocumentVersionRestoreRequest,
    KnowledgeDocumentTitleUpdateRequest,
    KnowledgeIngestionJobResponse,
    # 任务列表的分页响应。
    KnowledgeIngestionJobListResponse,
)

from ticketpilot.knowledge.application.jobs import (
    KnowledgeJobSubmissionError,
    submit_ingestion_job,
    # 保存原始文件并提交异步入库任务。
    submit_file_ingestion_job,
    # 将失败任务重新排队。
    retry_ingestion_job,

    # 重试过程中发生数据库等内部错误。
    KnowledgeJobRetryError,
    cancel_ingestion_job,
    KnowledgeJobCancellationError,
)

# 原始文件保存失败等存储异常。
from ticketpilot.knowledge.persistence.file_storage import (
    KnowledgeFileStorageError,
)

# 导入历史版本恢复 Service 和统一内部异常。
from ticketpilot.knowledge.application.versioning import (
    KnowledgeDocumentVersionRestoreError,
    restore_document_version,
)

# 导入知识检索服务、结果类型和统一异常。
from ticketpilot.knowledge.application.retrieval import (
    InvalidKnowledgeQueryError,
    KnowledgeRetrievalError,
    RetrievedKnowledgeChunk,
    retrieve_knowledge,
)

from ticketpilot.knowledge.persistence.job_repository import (
    KnowledgeJobRepositoryError,
    get_ingestion_job,
    # 按提交人范围分页查询任务。
    list_ingestion_jobs,
)


# 创建知识库路由分组。
router = APIRouter(
    # 当前文件中的接口统一使用这个地址前缀。
    prefix="/v1/knowledge",

    # Swagger 文档中显示在 knowledge 分组。
    tags=["knowledge"],
)


# 这个函数负责把知识文档 ORM 对象转换成 API 响应。
def to_knowledge_document_response(
    document: KnowledgeDocumentRecord,
) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        source_uri=document.source_uri,
        status=document.status,
        # 返回数据库中这篇文档真正的可见性。
        visibility=document.visibility,
        # chunks 是当前文档关联的全部知识片段。
        chunk_count=len(
            document.chunks
        ),

        embedding_model=(
            document.embedding_model
        ),
        embedding_dimensions=(
            document.embedding_dimensions
        ),
        version=document.version,
        created_by=document.created_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )

# 这个函数负责把 Repository 摘要转换成公开 API 响应。
def to_knowledge_document_summary_response(
    summary: (
        knowledge_repository
        .KnowledgeDocumentSummaryRow
    ),
) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=summary.id,
        title=summary.title,
        source_type=summary.source_type,
        source_uri=summary.source_uri,
        status=summary.status,
        visibility=summary.visibility,
        chunk_count=summary.chunk_count,
        embedding_model=(
            summary.embedding_model
        ),
        embedding_dimensions=(
            summary.embedding_dimensions
        ),
        version=summary.version,
        created_by=summary.created_by,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )

# 这个函数负责把 Repository 详情转换成公开 API 响应。
def to_knowledge_document_detail_response(
    detail: (
        knowledge_repository
        .KnowledgeDocumentDetailRow
    ),
) -> KnowledgeDocumentDetailResponse:
    return KnowledgeDocumentDetailResponse(
        id=detail.id,
        title=detail.title,
        raw_content=detail.raw_content,
        cleaned_content=(
            detail.cleaned_content
        ),
        source_type=detail.source_type,
        source_uri=detail.source_uri,
        status=detail.status,
        visibility=detail.visibility,
        processing_stage=(
            detail.processing_stage
        ),
        chunk_count=detail.chunk_count,
        embedding_model=(
            detail.embedding_model
        ),
        embedding_dimensions=(
            detail.embedding_dimensions
        ),
        cleaner_version=(
            detail.cleaner_version
        ),
        chunker_version=(
            detail.chunker_version
        ),
        version=detail.version,
        created_by=detail.created_by,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
    )

# 这个函数负责把 Repository 历史版本摘要转换成公开响应。
def to_knowledge_document_version_response(
    version: (
        knowledge_repository
        .KnowledgeDocumentVersionSummaryRow
    ),
) -> KnowledgeDocumentVersionResponse:
    return KnowledgeDocumentVersionResponse(
        version=version.version,
        title=version.title,
        source_type=version.source_type,
        source_uri=version.source_uri,
        embedding_model=(
            version.embedding_model
        ),
        embedding_dimensions=(
            version.embedding_dimensions
        ),
        cleaner_version=(
            version.cleaner_version
        ),
        chunker_version=(
            version.chunker_version
        ),
        created_by=version.created_by,
        created_at=version.created_at,
        superseded_by=(
            version.superseded_by
        ),
        superseded_at=(
            version.superseded_at
        ),
    )

# 这个函数负责把 Repository 历史版本详情转换成公开响应。
def to_knowledge_document_version_detail_response(
    detail: (
        knowledge_repository
        .KnowledgeDocumentVersionDetailRow
    ),
) -> KnowledgeDocumentVersionDetailResponse:
    return KnowledgeDocumentVersionDetailResponse(
        version=detail.version,
        title=detail.title,
        source_type=detail.source_type,
        source_uri=detail.source_uri,
        embedding_model=(
            detail.embedding_model
        ),
        embedding_dimensions=(
            detail.embedding_dimensions
        ),
        cleaner_version=(
            detail.cleaner_version
        ),
        chunker_version=(
            detail.chunker_version
        ),
        created_by=detail.created_by,
        created_at=detail.created_at,
        superseded_by=(
            detail.superseded_by
        ),
        superseded_at=(
            detail.superseded_at
        ),

        # 详情接口才会公开完整正文。
        raw_content=detail.raw_content,
        cleaned_content=(
            detail.cleaned_content
        ),
    )

# 这个函数负责把 Repository 历史版本分页结果转换成公开响应。
def to_knowledge_document_version_list_response(
    page: (
        knowledge_repository
        .KnowledgeDocumentVersionListPage
    ),

    # 星号后面的参数必须写出参数名称。
    *,

    # 当前查询的知识文档 ID。
    document_id: str,

    # 当前请求的分页参数。
    limit: int,
    offset: int,
) -> KnowledgeDocumentVersionListResponse:
    return KnowledgeDocumentVersionListResponse(
        document_id=document_id,
        current_version=page.current_version,

        # 把 tuple 中的每条内部结果转换成公开响应。
        items=[
            to_knowledge_document_version_response(
                version
            )
            for version in page.items
        ],

        total=page.total,
        limit=limit,
        offset=offset,
    )

# 根据 ID 查询单篇知识文档详情。
@router.get(
    # document_id 来自 URL 路径。
    "/documents/{document_id}",

    # 成功响应必须符合详情 Schema。
    response_model=KnowledgeDocumentDetailResponse,

    # 查询成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def get_knowledge_document(
    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以查看知识文档详情。
    current_user: SupervisorOrAdminUser,

    # 文档 ID 不能为空，并限制最大长度为 UUID 字符串长度。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],
) -> KnowledgeDocumentDetailResponse:
    # 当前阶段只使用该依赖执行身份和角色验证。
    _ = current_user

    try:
        # Repository 根据主键查询轻量详情对象。
        detail = (
            knowledge_repository
            .get_document_by_id(
                session,
                document_id,
            )
        )

    # 数据库连接或者 SQL 查询失败。
    except (
        knowledge_repository
        .KnowledgeRepositoryError
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # Repository 使用 None 表示数据库中没有这篇文档。
    if detail is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="知识文档不存在",
        )

    # 把内部详情对象转换成公开响应。
    return (
        to_knowledge_document_detail_response(
            detail
        )
    )
# 查询知识文档历史版本的业务流程：
#
# GET /v1/knowledge/documents/{document_id}/versions
# → 验证 JWT
# → 验证用户是 supervisor 或 admin
# → 验证 document_id、limit 和 offset
# → Repository 确认当前文档是否存在
# → Repository 分页查询历史版本
# → 把内部 Row/Page 转换成公开响应
# → 返回历史版本列表
@router.get(
    # 完整地址：
    # GET /v1/knowledge/documents/{document_id}/versions
    "/documents/{document_id}/versions",

    # 响应必须符合历史版本分页 Schema。
    response_model=(
        KnowledgeDocumentVersionListResponse
    ),

    # 查询成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def list_knowledge_document_versions(
    # 获取当前请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以查看历史版本。
    current_user: SupervisorOrAdminUser,

    # 从 URL 路径中获取知识文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],

    # 当前页面最多返回多少条历史版本。
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
        ),
    ] = 20,

    # 跳过前面的多少条历史版本。
    offset: Annotated[
        int,
        Query(
            ge=0,
        ),
    ] = 0,
) -> KnowledgeDocumentVersionListResponse:
    # 当前函数不需要直接读取用户字段，
    # 但这个依赖会完成登录和角色验证。
    _ = current_user

    try:
        # Repository 查询当前文档和历史版本分页数据。
        page = (
            knowledge_repository
            .list_document_versions(
                session,
                document_id,
                limit=limit,
                offset=offset,
            )
        )

    # 数据库连接或 SQL 查询失败。
    except (
        knowledge_repository
        .KnowledgeRepositoryError
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # Repository 返回 None，
    # 表示当前知识文档本身不存在。
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档不存在",
        )

    # 把 Repository 的内部分页结果转换成公开响应。
    return (
        to_knowledge_document_version_list_response(
            page,
            document_id=document_id,
            limit=limit,
            offset=offset,
        )
    )

# 查询单个历史版本详情的业务流程：
#
# GET /v1/knowledge/documents/{document_id}/versions/{version}
# → 验证 JWT
# → 验证用户是 supervisor 或 admin
# → 验证 document_id 和 version
# → Repository 查询指定历史版本
# → 转换成公开详情响应
# → 返回历史版本的完整正文
@router.get(
    # 完整地址示例：
    # GET /v1/knowledge/documents/document-001/versions/2
    "/documents/{document_id}/versions/{version}",

    # 成功响应必须符合历史版本详情 Schema。
    response_model=(
        KnowledgeDocumentVersionDetailResponse
    ),

    # 查询成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def get_knowledge_document_version(
    # 获取当前请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以查看历史版本详情。
    current_user: SupervisorOrAdminUser,

    # 从 URL 中获取知识文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],

    # 从 URL 中获取历史版本号。
    version: Annotated[
        int,
        Path(
            ge=1,
        ),
    ],
) -> KnowledgeDocumentVersionDetailResponse:
    # 当前函数不需要直接读取用户字段，
    # 但该依赖会完成登录和角色验证。
    _ = current_user

    try:
        # Repository 使用文档 ID 和版本号查询历史详情。
        detail = (
            knowledge_repository
            .get_document_version_by_number(
                session,
                document_id,
                version,
            )
        )

    # 数据库连接或者 SQL 查询失败。
    except (
        knowledge_repository
        .KnowledgeRepositoryError
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # 找不到指定历史版本。
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档历史版本不存在",
        )

    # 把 Repository 内部详情转换成公开响应。
    return (
        to_knowledge_document_version_detail_response(
            detail
        )
    )

# 恢复历史版本的业务流程：
#
# POST /v1/knowledge/documents/{document_id}/versions/{version}/restore
# → Pydantic 验证 expected_version
# → 验证 JWT
# → 验证用户是 supervisor 或 admin
# → Versioning Service 查询历史版本
# → 使用历史原始内容调用 Update Service
# → 保存恢复前的当前版本
# → 重新清洗、切块和生成向量
# → 当前版本号增加 1
# → 返回新创建的当前版本
@router.post(
    # 完整地址示例：
    # POST /v1/knowledge/documents/document-001/versions/1/restore
    "/documents/{document_id}/versions/{version}/restore",

    # 恢复成功后返回新的当前文档信息。
    response_model=KnowledgeDocumentResponse,

    # 恢复成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def restore_knowledge_document_version(
    # 客户端提交当前看到的文档版本号。
    request: KnowledgeDocumentVersionRestoreRequest,

    # 获取当前请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以恢复历史版本。
    current_user: SupervisorOrAdminUser,

    # 获取负责重新生成向量的 Provider。
    embedding_provider: EmbeddingProviderDependency,

    # 从 URL 中获取知识文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],

    # 从 URL 中获取要恢复的历史版本号。
    version: Annotated[
        int,
        Path(
            ge=1,
        ),
    ],
) -> KnowledgeDocumentResponse:
    try:
        # Versioning Service 执行完整恢复业务。
        restored_document = (
            restore_document_version(
                session=session,
                document_id=document_id,

                # 要使用哪个历史版本的内容。
                version=version,

                # 防止并发恢复覆盖其他人的修改。
                expected_version=(
                    request.expected_version
                ),

                # 操作人必须来自 JWT 当前用户，
                # 不能由客户端自己提交。
                restored_by=current_user.id,

                # 使用依赖注入提供的向量模型。
                embedding_provider=(
                    embedding_provider
                ),
            )
        )

    # 这些是可以预期的业务冲突。
    except (
        KnowledgeDocumentVersionConflictError,
        InvalidKnowledgeDocumentUpdateStateError,
        KnowledgeDocumentNoChangesError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    # 历史查询、数据库、清洗、切块或向量流程失败。
    except (
        KnowledgeDocumentVersionRestoreError
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # 没有找到指定历史版本，
    # 或恢复过程中当前文档已经不存在。
    if restored_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档历史版本不存在",
        )

    # 返回恢复后生成的新当前版本。
    return to_knowledge_document_response(
        restored_document
    )

# 这个接口只接收并保存任务，不在请求中生成向量。
@router.post(
    "/jobs",
    response_model=KnowledgeIngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_knowledge_job(
    request: KnowledgeDocumentCreateRequest,
    session: DatabaseSession,
    current_user: SupervisorOrAdminUser,
) -> KnowledgeIngestionJobResponse:
    raw_document = RawDocument(
        title=request.title,
        content=request.content,
    )

    try:
        job = submit_ingestion_job(
            session=session,
            raw_document=raw_document,
            requested_by=current_user.id,
            visibility=request.visibility,
        )
    except KnowledgeJobSubmissionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="提交知识入库任务失败",
        ) from error

    return KnowledgeIngestionJobResponse.model_validate(job)

@router.post(
    # 路由分组已有 /v1/knowledge 前缀。
    "/jobs/upload",

    # 返回任务信息，而不是已完成入库的文档。
    response_model=KnowledgeIngestionJobResponse,

    # 202 表示已接收处理请求，任务尚待 Worker 执行。
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_knowledge_file_job(
    # 当前请求使用的数据库会话。
    session: DatabaseSession,

    # 只有主管或管理员可以提交入库任务。
    current_user: SupervisorOrAdminUser,

    # 根据配置目录创建的原始文件存储服务。
    storage: KnowledgeFileStorageDependency,

    # 提交阶段只使用其中的上传检查能力。
    upload_service: KnowledgeFileUploadServiceDependency,

    # multipart 表单中的上传文件。
    file: Annotated[
        UploadFile,
        File(description="等待后台处理的 TXT、Markdown、PDF 或 DOCX 文件"),
    ],

    # 本接口要求明确填写标题。
    # 没有默认值，因此是必填表单字段。
    title: Annotated[
        str,
        Form(min_length=1, max_length=200),
    ],

    # multipart 普通表单字段，默认创建内部可见文档。
    visibility: Annotated[
        Literal["internal", "restricted"],
        Form(description="文档可见性：internal 或 restricted"),
    ] = "internal",
) -> KnowledgeIngestionJobResponse:
    """接收上传文件，创建异步知识入库任务。

    正常流程：
        验证登录身份和提交权限
        → 有界读取上传文件并关闭上传临时文件
        → Service 检查输入、保存原始文件、提交 queued 任务
        → 返回 HTTP 202 和任务信息。

    异常处理：
        上传过大返回 413，不支持的文件类型返回 415，
        输入校验失败返回 422，文件或任务保存失败返回 500。

    返回：
        已提交的任务信息，可用其中的 id 查询处理状态。

    调用约定：
        此请求不执行解析、OCR 或向量生成。
        后续需要运行 Worker；202 不代表文档已经入库成功。
    """

    # 客户端可能没有提供文件名，统一交给 Service 检查。
    filename = file.filename or ""

    try:
        # 最多读取“大小限制 + 1”字节，避免无限制读入内存。
        # 多出的一个字节用于判断文件是否超过限制。
        content = file.file.read(
            upload_service.max_file_size_bytes + 1
        )
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="上传文件读取失败",
        ) from error
    finally:
        # 关闭 FastAPI 接收上传时使用的临时文件。
        # 这里不是删除 storage.save() 保存的原始文件。
        file.file.close()

    try:
        job = submit_file_ingestion_job(
            session=session,
            storage=storage,
            upload_service=upload_service,
            title=title,
            filename=filename,
            content_type=file.content_type,
            content=content,

            # 提交人必须来自登录身份，不能让客户端指定。
            requested_by=current_user.id,

            visibility=visibility,
        )
    except KnowledgeFileTooLargeError as error:
        # 它是 KnowledgeFileValidationError 的子类，
        # 所以要放在通用校验异常前面。
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except UnsupportedKnowledgeFileTypeError as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(error),
        ) from error
    except KnowledgeFileValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except KnowledgeFileStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="原始文件保存失败",
        ) from error
    except KnowledgeJobSubmissionError as error:
        # Service 的异常信息由项目自身生成。
        # 提交状态不确定时，其中包含用于核实的任务 ID。
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    # 从 ORM 属性读取公开字段。
    # 响应 Schema 不包含 storage_key 或服务器磁盘路径。
    return KnowledgeIngestionJobResponse.model_validate(job)

# 查询当前用户有权查看的入库任务列表。
@router.get(
    "/jobs",
    response_model=KnowledgeIngestionJobListResponse,
)
def list_knowledge_jobs(
    # FastAPI 为当前请求提供数据库 Session。
    session: DatabaseSession,

    # 验证登录身份，并要求角色为主管或管理员。
    current_user: SupervisorOrAdminUser,

    # 可选的任务状态筛选。
    # URL 中的参数名叫 status，Python 函数中叫 job_status。
    # 不传时为 None，表示查询所有状态。
    job_status: Annotated[
        KnowledgeIngestionJobStatus | None,
        Query(
            alias="status",
            description="按任务状态筛选；不传则查询所有状态",
        ),
    ] = None,

    # 从 URL 查询参数读取每页数量。
    # 例如 /jobs?limit=10，表示每页最多返回 10 条。
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,

    # 从 URL 查询参数读取跳过的条数。
    # 例如 limit=10&offset=10，表示读取第二页。
    offset: Annotated[
        int,
        Query(ge=0),
    ] = 0,
) -> KnowledgeIngestionJobListResponse:
    """分页返回当前用户有权查看的入库任务。

    正常流程：
        验证身份和分页参数 → 根据角色确定查询范围
        → 查询任务及总数 → 转换为公开响应。

    异常处理：
        未登录或角色不允许时，由身份依赖拒绝请求。
        分页参数或任务状态不合法时，FastAPI 返回 422。
        数据库查询失败时返回 500。

    返回：
        当前页任务、符合条件的总数、limit 和 offset。

    调用约定：
        管理员查看所有任务，主管只查看自己提交的任务。
        查询范围由服务端确定，不接收客户端指定的提交人。
        只查询，不执行入库，也不修改任务状态。
    """

    # None 表示管理员查询全部提交人的任务。
    # 主管则只能使用自己的用户 ID 作为查询条件。
    requested_by = (
        None
        if current_user.role == "admin"
        else current_user.id
    )

    try:
        page = list_ingestion_jobs(
            session=session,
            requested_by=requested_by,
            # 把接口接收到的状态枚举传给数据库查询函数。
            job_status=job_status,
            limit=limit,
            offset=offset,
        )
    except KnowledgeJobRepositoryError as error:
        # 向客户端返回统一错误，不暴露数据库内部信息。
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询知识入库任务列表失败",
        ) from error

    return KnowledgeIngestionJobListResponse(
        # 逐条将数据库 ORM 对象转换为公开响应。
        items=[
            KnowledgeIngestionJobResponse.model_validate(job)
            for job in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )

# 这个接口负责查询一条入库任务的当前状态。
@router.get(
    "/jobs/{job_id}",
    response_model=KnowledgeIngestionJobResponse,
)
def get_knowledge_job(
    job_id: Annotated[str, Path(min_length=1, max_length=36)],
    session: DatabaseSession,
    current_user: SupervisorOrAdminUser,
) -> KnowledgeIngestionJobResponse:
    try:
        job = get_ingestion_job(session, job_id)
    except KnowledgeJobRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询知识入库任务失败",
        ) from error

    # 不存在，或其他主管提交的任务：都返回 404。
    if job is None or (
        current_user.role != "admin"
        and job.requested_by != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识入库任务不存在",
        )

    return KnowledgeIngestionJobResponse.model_validate(job)

@router.post(
    # 完整地址：POST /v1/knowledge/jobs/{job_id}/retry
    "/jobs/{job_id}/retry",

    # 继续复用任务响应，不暴露内部文件路径。
    response_model=KnowledgeIngestionJobResponse,

    # 表示已经接收重试请求，任务重新等待 Worker。
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_knowledge_job(
    # 从 URL 取得任务 ID，限制基本长度。
    job_id: Annotated[
        str,
        Path(min_length=1, max_length=36),
    ],

    # 当前请求使用的数据库会话。
    session: DatabaseSession,

    # 只允许主管或管理员进入该接口。
    current_user: SupervisorOrAdminUser,
) -> KnowledgeIngestionJobResponse:
    """将有权管理的失败任务重新放回等待队列。

    正常流程：
        查询任务
        → 检查当前用户是否有权管理
        → Service 原子地将 failed 改为 queued 并提交
        → 返回 HTTP 202 和任务信息。

    异常处理：
        任务不存在或无权访问时返回 404。
        当前状态不允许重试时返回 409。
        数据库查询或重试失败时返回 500。

    返回：
        已重新排队的任务记录。

    调用约定：
        管理员可以重试所有任务，主管只能重试自己提交的任务。
        不在请求中执行入库，也不增加处理次数。
        Worker 真正领取任务时，才增加 attempt_count。
    """

    try:
        # 先查询任务，检查当前用户能否管理它。
        job = get_ingestion_job(session, job_id)
    except KnowledgeJobRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询知识入库任务失败",
        ) from error

    # 与任务查询接口采用相同的访问规则。
    # 无权访问时也返回 404，避免透露其他人的任务是否存在。
    if job is None or (
        current_user.role != "admin"
        and job.requested_by != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识入库任务不存在",
        )

    try:
        # Repository 在 UPDATE 的 WHERE 中检查 status='failed'。
        # 即使两个请求同时重试，也只有符合条件的更新能成功。
        retried_job = retry_ingestion_job(
            session=session,
            job_id=job_id,
        )
    except KnowledgeJobRetryError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="重试知识入库任务失败",
        ) from error

    # 查询时存在，但原子更新没有匹配：
    # 可能本来就不是 failed，也可能刚被其他请求重新排队。
    if retried_job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务当前状态不允许重试",
        )

    return KnowledgeIngestionJobResponse.model_validate(retried_job)

# 更新知识文档的业务流程：
#
# PUT /v1/knowledge/documents/{document_id}
# → Pydantic 验证 title、content、expected_version
# → 验证 JWT 和 supervisor/admin 权限
# → 把请求数据转换成 RawDocument
# → update_document() 检查文档状态和版本号
# → 保存当前版本到历史版本表
# → 重新清洗、分块并生成向量
# → 替换旧知识片段
# → 文档版本号加 1
# → 统一提交数据库事务
# → 返回更新后的文档信息
@router.put(
    # 完整地址：
    # PUT /v1/knowledge/documents/{document_id}
    "/documents/{document_id}",

    # 更新成功后返回知识文档信息。
    response_model=KnowledgeDocumentResponse,

    # 更新成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)


def update_knowledge_document(
    # 客户端提交的新标题、新正文和预期版本号。
    request: KnowledgeDocumentUpdateRequest,

    # 获取当前请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以更新知识文档。
    current_user: SupervisorOrAdminUser,

    # 获取负责生成文本向量的 Provider。
    embedding_provider: EmbeddingProviderDependency,

    # 从 URL 路径中获取要更新的文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],
) -> KnowledgeDocumentResponse:
    # 把 API 请求数据转换成业务层使用的原始文档对象。
    raw_document = RawDocument(
        title=request.title,
        content=request.content,
    )

    try:
        # 调用更新 Service 执行完整业务流程。
        updated_document = update_document(
            session=session,
            document_id=document_id,
            raw_document=raw_document,

            # 客户端读取文档时看到的版本号。
            expected_version=(
                request.expected_version
            ),

            # 修改人必须来自 JWT 当前用户，
            # 不能让客户端自己提交，防止伪造身份。
            updated_by=current_user.id,

            # 使用依赖注入提供的向量模型。
            embedding_provider=(
                embedding_provider
            ),
        )

    # 下面三种情况都是业务冲突，返回 HTTP 409。
    except (
        KnowledgeDocumentVersionConflictError,
        InvalidKnowledgeDocumentUpdateStateError,
        KnowledgeDocumentNoChangesError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,

            # 把具体业务原因返回给客户端。
            detail=str(error),
        ) from error

    # 数据库或向量模型等内部流程失败。
    except KnowledgeDocumentUpdateError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),

            # 不把内部异常细节暴露给客户端。
            detail="知识库服务暂时不可用",
        ) from error

    # Service 返回 None，表示没有找到对应文档。
    if updated_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档不存在",
        )

    # 把更新后的 ORM 对象转换成公开 API 响应。
    return to_knowledge_document_response(
        updated_document
    )

# 只修改知识文档标题的业务流程：
# 验证请求和权限 → 检查版本 → 保存旧版快照
# → 只改标题和版本号 → 提交事务；正文和向量不变。
@router.patch(
    "/documents/{document_id}/title",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_200_OK,
)
def update_knowledge_document_title(
    request: KnowledgeDocumentTitleUpdateRequest,
    session: DatabaseSession,
    current_user: SupervisorOrAdminUser,
    document_id: Annotated[
        str,
        Path(min_length=1, max_length=36),
    ],
) -> KnowledgeDocumentResponse:
    try:
        updated_document = update_document_title(
            session=session,
            document_id=document_id,
            title=request.title,
            expected_version=request.expected_version,
            updated_by=current_user.id,
        )
    except (
        KnowledgeDocumentVersionConflictError,
        InvalidKnowledgeDocumentUpdateStateError,
        KnowledgeDocumentNoChangesError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except KnowledgeDocumentUpdateError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="知识库服务暂时不可用",
        ) from error

    if updated_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档不存在",
        )

    return to_knowledge_document_response(updated_document)


# 根据 ID 归档一篇知识文档。
@router.post(
    # 完整地址：
    # POST /v1/knowledge/documents/{document_id}/archive
    "/documents/{document_id}/archive",

    # 归档成功后不返回响应正文。
    status_code=status.HTTP_204_NO_CONTENT,
)
def archive_knowledge_document(
    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以归档知识文档。
    current_user: SupervisorOrAdminUser,

    # 从 URL 路径中接收知识文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],
) -> Response:
    """
    归档指定知识文档。

    业务流程：
    验证身份和角色
    → Management Service 查询文档
    → 把状态改成 archived
    → 提交数据库事务
    → 返回 HTTP 204
    """

    # 当前阶段只使用该对象完成身份和权限验证。
    _ = current_user

    try:
        # Service 执行归档业务并控制数据库事务。
        archived = archive_document(
            session=session,
            document_id=document_id,
        )

    # Service 已经隐藏了具体数据库异常。
    except KnowledgeManagementError as error:
        # 不向客户端泄漏内部数据库错误。
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # False 表示 Repository 没有找到这篇文档。
    if not archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档不存在",
        )

    # 归档成功。
    # HTTP 204 只表示操作成功，不携带 JSON。
    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )

# 根据 ID 恢复一篇已经归档的知识文档。
@router.post(
    # 完整地址：
    # POST /v1/knowledge/documents/{document_id}/restore
    "/documents/{document_id}/restore",

    # 恢复成功后不返回响应正文。
    status_code=status.HTTP_204_NO_CONTENT,
)
def restore_knowledge_document(
    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以恢复知识文档。
    current_user: SupervisorOrAdminUser,

    # 从 URL 路径中接收知识文档 ID。
    document_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=36,
        ),
    ],
) -> Response:
    """
    恢复指定的知识文档。

    业务流程：
    验证身份和角色
    → Management Service 查询文档
    → 检查文档当前状态
    → archived 修改为 ready
    → 提交数据库事务
    → 返回 HTTP 204
    """

    # 当前阶段只使用该对象完成身份和角色验证。
    _ = current_user

    try:
        # Service 执行状态检查、恢复和事务提交。
        restored = restore_document(
            session=session,
            document_id=document_id,
        )

    # 文档存在，但是当前状态不能执行恢复。
    except InvalidKnowledgeDocumentStateError as error:
        raise HTTPException(
            # 409 表示请求本身正确，
            # 但是与资源当前状态发生冲突。
            status_code=status.HTTP_409_CONFLICT,

            # 这是由我们控制的业务错误，可以返回给客户端。
            detail=str(error),
        ) from error

    # 数据库查询或者提交失败。
    except KnowledgeManagementError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # False 表示数据库中没有这篇文档。
    if not restored:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识文档不存在",
        )

    # 文档已经恢复为 ready，不需要返回 JSON。
    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )


# 分页查询知识文档列表。
@router.get(
    # 完整地址是 GET /v1/knowledge/documents。
    "/documents",

    # 成功响应必须符合分页列表 Schema。
    response_model=KnowledgeDocumentListResponse,

    # 查询成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def list_knowledge_documents(
    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以管理知识文档。
    current_user: SupervisorOrAdminUser,

    # 每次最多返回 100 篇，默认返回 20 篇。
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
        ),
    ] = 20,

    # offset 不能是负数。
    offset: Annotated[
        int,
        Query(
            ge=0,
        ),
    ] = 0,
) -> KnowledgeDocumentListResponse:
    # 当前阶段只需要依赖注入完成权限验证。
    # 后续增加 tenant_id 时，会用当前用户过滤企业数据。
    _ = current_user

    try:
        # Repository 执行总数统计和分页摘要查询。
        page = knowledge_repository.list_documents(
            session,
            limit=limit,
            offset=offset,
        )

    # 数据库连接或者 SQL 查询失败。
    except (
        knowledge_repository
        .KnowledgeRepositoryError
    ) as error:
        # 不向客户端泄露数据库内部信息。
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # 把 Repository 结果转换成公开响应。
    return KnowledgeDocumentListResponse(
        items=[
            to_knowledge_document_summary_response(
                summary
            )
            for summary in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )

# 上传知识文件的业务流程：
#
# POST /v1/knowledge/documents/upload
# → 验证 JWT 和用户角色
# → 有界读取上传文件
# → 验证文件名、大小和文件类型
# → 把 TXT、Markdown 、PDF 或 DOCX 解析成纯文本
# → 创建 RawDocument
# → 调用现有 ingest_document()
# → 清洗、分块、生成向量并保存数据库
@router.post(
    # 完整地址：
    # POST /v1/knowledge/documents/upload
    "/documents/upload",

    # 成功响应与手动创建文档保持一致。
    response_model=KnowledgeDocumentResponse,

    # 上传并创建成功返回 HTTP 201。
    status_code=status.HTTP_201_CREATED,
)
def upload_knowledge_document(
    # 获取当前请求独享的数据库 Session。
    session: DatabaseSession,

    # 只有主管或管理员可以上传知识文件。
    current_user: SupervisorOrAdminUser,

    # 获取生成知识向量的 Provider。
    embedding_provider: EmbeddingProviderDependency,

    # 获取文件验证和解析 Service。
    upload_service: (
        KnowledgeFileUploadServiceDependency
    ),

    # 接收客户端上传的文件。
    file: Annotated[
        UploadFile,
        File(
            description=(
                    "需要导入知识库的 TXT、Markdown 、PDF 或 DOCX 文件"
            ),
        ),
    ],

    # 用户可以覆盖从文件中自动提取的标题。
    title: Annotated[
        str | None,
        Form(
            max_length=200,
        ),
    ] = None,

    # 文件上传使用表单字段，不是 JSON 请求体。
    visibility: Annotated[
            Literal["internal", "restricted"],
            Form(description="文档可见性：internal 或 restricted"),
    ] = "internal",
) -> KnowledgeDocumentResponse:
    """上传、解析并导入一篇知识文件。"""

    # UploadFile.filename 有可能是 None，
    # 统一转换成空字符串后交给 Service 验证。
    filename = file.filename or ""

    try:
        # 只读取“最大限制 + 1”个字节。
        # 如果真的读到多出来的 1 个字节，
        # Service 就能判断文件已经超出限制。
        content = file.file.read(
            upload_service.max_file_size_bytes
            + 1
        )

    # 操作系统或临时文件读取失败。
    except OSError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="上传文件读取失败",
        ) from error

    finally:
        # 无论读取成功还是失败，都关闭临时文件。
        file.file.close()

    try:
        # 验证文件名和大小，然后自动选择解析器。
        parsed_file = upload_service.parse_upload(
            filename=filename,
            content_type=file.content_type,
            content=content,
        )

    # 文件超过系统允许的大小。
    # 必须放在 KnowledgeFileValidationError 前面，
    # 因为它是 ValidationError 的子类。
    except KnowledgeFileTooLargeError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_413_CONTENT_TOO_LARGE
            ),
            detail=str(error),
        ) from error

    # 系统暂时不支持该文件扩展名。
    except UnsupportedKnowledgeFileTypeError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            ),
            detail=str(error),
        ) from error

    # 文件名、文件内容或者编码不合法。
    except (
        KnowledgeFileValidationError,
        KnowledgeFileParsingError,
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(error),
        ) from error

    # 如果用户提交了有效标题，优先使用用户标题。
    requested_title = (
        title.strip()
        if title is not None
        else ""
    )

    # 用户没有填写标题时，
    # 使用解析器从文档或文件名中取得的标题。
    document_title = (
        requested_title
        or parsed_file.title
        or filename
    )

    # 把文件解析结果转换成现有入库流程认识的对象。
    raw_document = RawDocument(
        title=document_title,
        content=parsed_file.text,

        # 标记这篇文档来自上传文件。
        source_type=KnowledgeSourceType.FILE,

        # 当前阶段使用原始文件名记录来源。
        source_uri=filename,

        # 把解析器的页面对象，转换成业务层的页面对象。
        source_pages=tuple(
            SourcePage(
                # 左边是 SourcePage 的参数名；
                # 右边是解析器返回的这一页的数据。
                page_number=page.page_number,
                text=page.text,
            )
            for page in parsed_file.pages
        ),
    )

    try:
        # 复用现有的清洗、分块、向量化和保存流程。
        saved_document = ingest_document(
            session=session,
            raw_document=raw_document,
            created_by=current_user.id,
            embedding_provider=(
                embedding_provider
            ),
            # 上传时就把权限与文档一起保存。
            visibility=visibility,
        )

    # 向量模型、清洗或数据库操作失败。
    except KnowledgeIngestionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # 把内部 ORM 对象转换成公开 API 响应。
    return to_knowledge_document_response(
        saved_document
    )

# 创建并处理一篇手动输入的知识文档。
@router.post(
    # 完整地址是 POST /v1/knowledge/documents。
    "/documents",

    # 成功响应必须符合 KnowledgeDocumentResponse。
    response_model=KnowledgeDocumentResponse,

    # 创建成功返回 HTTP 201。
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_document(
    # 接收并验证客户端提交的标题和正文。
    request: KnowledgeDocumentCreateRequest,

    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 验证 JWT，并限制为主管或管理员。
    current_user: SupervisorOrAdminUser,

    # 获取缓存的真实 Embedding Provider。
    embedding_provider: EmbeddingProviderDependency,
) -> KnowledgeDocumentResponse:
    # HTTP 请求只允许手动输入文本，
    # 所以 source_type 使用 RawDocument 的默认 manual。
    raw_document = RawDocument(
        title=request.title,
        content=request.content,
    )

    try:
        # 调用业务层完成清洗、切块、向量和数据库写入。
        saved_document = ingest_document(
            session=session,
            raw_document=raw_document,

            # created_by 必须来自已经验证的当前用户，
            # 不能相信客户端自己提交的用户 ID。
            created_by=current_user.id,

            embedding_provider=(
                embedding_provider
            ),
            # 创建时就确定权限，避免受限文档先以 internal 保存。
            visibility=request.visibility,
        )

    # 捕获业务层转换后的统一入库异常。
    except KnowledgeIngestionError as error:
        # 不向客户端泄露 Ollama、SQL 或服务器内部信息。
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识库服务暂时不可用",
        ) from error

    # 把内部 ORM 对象转换成安全的公开响应。
    return to_knowledge_document_response(
        saved_document
    )

# 这个函数负责把内部检索结果转换成公开 API 响应。
def to_knowledge_search_result_response(
    chunk: RetrievedKnowledgeChunk,
) -> KnowledgeSearchResultResponse:
    return KnowledgeSearchResultResponse(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        document_version=(
            chunk.document_version
        ),
        source_type=chunk.source_type,
        source_uri=chunk.source_uri,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        # 内部结果使用只读 tuple；公开 JSON 响应使用数组。
        page_numbers=list(
            chunk.page_numbers
        ),

        # 限制到 API 约定的 0 到 1 范围，
        # 避免浮点计算产生 1.0000000001。
        similarity=max(
            0.0,
            min(
                1.0,
                chunk.similarity,
            ),
        ),
    )

# 根据用户问题检索相关知识片段。
@router.post(
    # 完整地址是 POST /v1/knowledge/search。
    "/search",

    # 成功响应必须符合知识检索响应模型。
    response_model=KnowledgeSearchResponse,

    # 查询成功返回 HTTP 200。
    status_code=status.HTTP_200_OK,
)
def search_knowledge(
    # 接收并验证查询问题、top_k 和最低相似度。
    request: KnowledgeSearchRequest,

    # 获取本次请求独享的数据库 Session。
    session: DatabaseSession,

    # 要求用户必须登录。
    # agent、supervisor 和 admin 都可以检索知识。
    current_user: CurrentUser,

    # 获取用于生成问题向量的 Embedding Provider。
    embedding_provider: EmbeddingProviderDependency,
) -> KnowledgeSearchResponse:

    # 权限来自认证后的用户，不能由请求内容自行指定。
    allowed_visibilities = allowed_visibilities_for_role(
        current_user.role
    )

    try:
        # 调用 Retrieval Service 完成问题向量和数据库检索。
        retrieved_chunks = retrieve_knowledge(
            session=session,
            question=request.query,
            embedding_provider=(
                embedding_provider
            ),
            top_k=request.top_k,
            min_similarity=(
                request.min_similarity
            ),
            # 让数据库只检索该用户有权查看的文档。
            allowed_visibilities=allowed_visibilities,
        )

    # 正常情况下非法参数会先被 Pydantic 拦截。
    # 这里继续捕获是为了保护业务层边界。
    except InvalidKnowledgeQueryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(error),
        ) from error

    # 向量服务或者数据库检索失败。
    except KnowledgeRetrievalError as error:
        # 不向客户端泄露 Ollama、SQL 或数据库细节。
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="知识检索服务暂时不可用",
        ) from error

    # 把每一个内部检索结果转换成公开响应对象。
    response_results = [
        to_knowledge_search_result_response(
            chunk
        )
        for chunk in retrieved_chunks
    ]

    # 返回完整的知识检索响应。
    return KnowledgeSearchResponse(
        # Service 内部也会执行 strip()，
        # API 响应保持相同的规范化结果。
        query=request.query.strip(),

        # 结果可能为 0 条。
        result_count=len(
            response_results
        ),

        results=response_results,
    )

@router.post(
    "/jobs/{job_id}/cancel",
    response_model=KnowledgeIngestionJobResponse,
    status_code=status.HTTP_200_OK,
    responses={
        202: {
            "model": KnowledgeIngestionJobResponse,
            "description": "取消申请已接收，等待 Worker 停止",
        },
    },
)
def cancel_knowledge_job(
    # URL 中准备取消的任务 ID。
    job_id: Annotated[
        str,
        Path(min_length=1, max_length=36),
    ],

    # 当前请求的数据库会话。
    session: DatabaseSession,

    # 当前登录的主管或管理员。
    current_user: SupervisorOrAdminUser,

    # FastAPI 注入的响应对象，用于设置 200 或 202。
    response: Response,
) -> KnowledgeIngestionJobResponse:
    """取消任务或登记取消申请。

    排队任务取消完成返回 200。
    运行任务登记取消申请返回 202。
    重复申请返回当前取消状态。

    不存在或越权返回 404。
    已成功或失败返回 409。
    数据库异常返回统一的 500。
    """

    try:
        job = get_ingestion_job(session, job_id)
    except KnowledgeJobRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询知识入库任务失败",
        ) from error

    if job is None or (
        current_user.role != "admin"
        and job.requested_by != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识入库任务不存在",
        )

    try:
        result = cancel_ingestion_job(
            session=session,
            job_id=job_id,
        )
    except KnowledgeJobCancellationError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="取消知识入库任务失败",
        ) from error

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务当前状态不允许取消",
        )

    # 已经取消完成返回 200；仍等待停止返回 202。
    response.status_code = (
        status.HTTP_202_ACCEPTED
        if result.status
        == KnowledgeIngestionJobStatus.CANCELLING.value
        else status.HTTP_200_OK
    )

    return KnowledgeIngestionJobResponse.model_validate(
        result
    )