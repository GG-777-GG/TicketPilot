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
    HTTPException,
    status,
)

# 导入请求独享的数据库 Session 依赖。
from ticketpilot.core.dependencies import (
    DatabaseSession,
)

# 只有主管或管理员才能修改企业知识库。
from ticketpilot.identity.dependencies import (
    SupervisorOrAdminUser,
    #登录后都可以检索知识
    CurrentUser,
)

# 导入知识库使用的向量模型依赖。
from ticketpilot.knowledge.dependencies import (
    EmbeddingProviderDependency,
)

# 导入入库流水线内部使用的原始文档。
from ticketpilot.knowledge.documents import (
    RawDocument,
)

# 导入完整入库服务和统一异常。
from ticketpilot.knowledge.ingestion import (
    KnowledgeIngestionError,
    ingest_document,
)

# 导入知识文档 ORM 类型。
from ticketpilot.knowledge.models import (
    KnowledgeDocumentRecord,
)

# 导入 HTTP 请求和响应 Schema。
from ticketpilot.knowledge.schemas import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResultResponse,
)

# 导入知识检索服务、结果类型和统一异常。
from ticketpilot.knowledge.retrieval import (
    InvalidKnowledgeQueryError,
    KnowledgeRetrievalError,
    RetrievedKnowledgeChunk,
    retrieve_knowledge,
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
    # 当前第一阶段只验证用户已经登录。
    # 后续增加 tenant_id 和文档 ACL 时，
    # 会使用 current_user 过滤用户有权查看的知识。
    _ = current_user

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