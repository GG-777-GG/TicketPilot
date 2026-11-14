"""
实现知识文档历史版本业务。

恢复历史版本的业务流程：
Router 接收文档 ID、历史版本号和 expected_version
→ Versioning Service 查询历史版本内容
→ 结束历史版本查询产生的只读事务
→ 把历史标题和正文转换成 RawDocument
→ 调用 Update Service 执行正常更新流程
→ 保存当前版本到历史表
→ 重新清洗、切块和生成向量
→ 当前文档版本号增加 1
→ Update Service 统一提交事务

注意：
恢复历史版本不会修改成旧版本号。

例如：
当前版本是 3，恢复历史版本 1
→ 历史版本 1 的内容成为新的版本 4
→ 原来的版本 3 也会进入历史表
"""

# SQLAlchemyError 表示数据库事务操作失败。
from sqlalchemy.exc import SQLAlchemyError

# Session 表示当前请求使用的数据库会话。
from sqlalchemy.orm import Session

# RawDocument 表示尚未清洗的标题和正文。
from ticketpilot.knowledge.domain.documents import (
    RawDocument,
)

# EmbeddingProvider 负责生成知识片段向量。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 当前文档 ORM 类型，用作返回值类型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
)

# 导入历史版本查询和 Repository 统一异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    get_document_version_by_number,
)

# 导入现有更新 Service 和内部错误。
from ticketpilot.knowledge.application.updating import (
    KnowledgeDocumentUpdateError,
    update_document,
)


# 表示恢复历史版本的内部流程失败。
class KnowledgeDocumentVersionRestoreError(
    RuntimeError
):
    """知识文档历史版本恢复失败。"""


# 这个函数负责把指定历史内容恢复成一个新的当前版本。
def restore_document_version(
    *,
    # 当前请求使用的数据库 Session。
    session: Session,

    # 要恢复哪一篇知识文档。
    document_id: str,

    # 要使用哪一个历史版本的内容。
    version: int,

    # 客户端读取当前文档时看到的版本号。
    expected_version: int,

    # 执行本次恢复操作的用户 ID。
    restored_by: str,

    # 负责重新生成知识片段向量。
    embedding_provider: EmbeddingProvider,
) -> KnowledgeDocumentRecord | None:
    """
    把历史内容恢复成一个新的当前版本。

    返回更新后的当前文档。
    找不到指定历史版本时返回 None。
    """

    try:
        # 第一步：查询指定历史版本的完整内容。
        historical_version = (
            get_document_version_by_number(
                session,
                document_id,
                version,
            )
        )

        # 没有找到指定历史版本。
        if historical_version is None:
            # 结束 Repository 查询产生的只读事务。
            session.rollback()
            return None

        # 把历史标题和历史原始正文转换成 RawDocument。
        #
        # 后续仍然会使用当前版本的 Cleaning 和 Chunking 规则
        # 重新处理，不能直接复制旧的 cleaned_content。
        raw_document = RawDocument(
            title=historical_version.title,
            content=(
                historical_version.raw_content
            ),
        )

        # 历史版本查询已经完成。
        #
        # 在重新清洗、切块和调用 Ollama 之前结束只读事务，
        # 避免长时间占用数据库事务。
        session.rollback()

    # 查询历史版本或者结束事务失败。
    except (
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        session.rollback()

        raise KnowledgeDocumentVersionRestoreError(
            "读取待恢复的知识文档历史版本失败"
        ) from error

    try:
        # 第二步：复用现有 Update Service。
        #
        # Update Service 会负责：
        # 1. 检查当前状态。
        # 2. 检查 expected_version。
        # 3. 清洗历史原始正文。
        # 4. 重新切块和生成向量。
        # 5. 保存恢复前的当前版本。
        # 6. 版本号加 1。
        # 7. commit 或 rollback。
        return update_document(
            session=session,
            document_id=document_id,
            raw_document=raw_document,
            expected_version=expected_version,
            updated_by=restored_by,
            embedding_provider=(
                embedding_provider
            ),
        )

    # Update Service 已经完成 rollback，
    # 这里只把内部更新错误转换成恢复业务的统一错误。
    except KnowledgeDocumentUpdateError as error:
        raise KnowledgeDocumentVersionRestoreError(
            "恢复知识文档历史版本失败"
        ) from error