"""
实现知识文档管理业务。

归档流程：
Router 接收归档请求
→ Management Service 查询文档 ORM 对象
→ 把文档状态修改为 archived
→ Session 提交事务
→ 文档停止参与 RAG 检索

异常流程：
Repository 查询失败或者数据库提交失败
→ Session 回滚事务
→ 抛出统一的 KnowledgeManagementError
"""


# SQLAlchemyError 表示数据库操作发生错误。
from sqlalchemy.exc import SQLAlchemyError

# Session 表示当前数据库会话和事务。
from sqlalchemy.orm import Session

# 导入知识文档状态枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)

# 导入 Repository 查询函数和统一异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    get_document_record_by_id,
)


# 表示知识文档管理操作失败。
class KnowledgeManagementError(RuntimeError):
    """知识文档管理操作执行失败。"""

# 表示文档当前状态不允许执行指定操作。
class InvalidKnowledgeDocumentStateError(
    ValueError
):
    """知识文档当前状态不允许执行管理操作。"""

# 这个函数负责把知识文档归档。
def archive_document(
    *,
    # 当前业务流程使用的数据库 Session。
    session: Session,

    # 要归档的知识文档 ID。
    document_id: str,
) -> bool:
    """
    根据文档 ID 归档知识文档。

    返回 True 表示文档存在并已归档。
    返回 False 表示文档不存在。
    """

    try:
        # Repository 根据主键查询可修改的 ORM 对象。
        document = get_document_record_by_id(
            session,
            document_id,
        )

        # 文档不存在，不需要执行数据库更新。
        if document is None:
            return False

        # 把文档状态修改为已归档。
        document.status = (
            KnowledgeDocumentStatus
            .ARCHIVED
            .value
        )

        # 归档后的文档不再处于任何处理阶段。
        document.processing_stage = None

        # 提交事务，把状态变化真正保存到数据库。
        session.commit()

        # 文档存在并且归档成功。
        return True

    # 捕获 Repository 查询错误和数据库提交错误。
    except (
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        # 撤销当前事务中尚未提交的修改。
        session.rollback()

        # 转换成管理 Service 对外暴露的统一异常。
        raise KnowledgeManagementError(
            "知识文档归档失败"
        ) from error

# 这个函数负责恢复一篇已经归档的知识文档。
def restore_document(
    *,
    # 当前业务流程使用的数据库 Session。
    session: Session,

    # 要恢复的知识文档 ID。
    document_id: str,
) -> bool:
    """
    恢复已经归档的知识文档。

    业务流程：
    根据 ID 查询文档
    → 检查当前文档状态
    → archived 修改为 ready
    → 提交数据库事务
    → 文档重新参与 RAG 检索

    返回 True 表示文档已经处于 ready 状态。
    返回 False 表示文档不存在。
    """

    try:
        # 查询当前 Session 管理的文档 ORM 对象。
        document = get_document_record_by_id(
            session,
            document_id,
        )

        # 文档不存在，由 Router 转换成 HTTP 404。
        if document is None:
            return False

        # 如果文档已经是 ready，
        # 说明目标状态已经达到，直接返回成功。
        #
        # 这样前端因为网络问题重复发送请求时，
        # 不会产生错误。
        if document.status == (
            KnowledgeDocumentStatus.READY.value
        ):
            return True

        # 只有 archived 状态可以恢复成 ready。
        if document.status != (
            KnowledgeDocumentStatus.ARCHIVED.value
        ):
            raise InvalidKnowledgeDocumentStateError(
                "只有已归档的知识文档可以恢复"
            )

        # 恢复为可以参与 RAG 检索的状态。
        document.status = (
            KnowledgeDocumentStatus.READY.value
        )

        # 恢复后的文档不处于清洗、切块等处理中间阶段。
        document.processing_stage = None

        # 提交事务，把状态变化保存到数据库。
        session.commit()

        # 文档已经成功恢复。
        return True

    # 业务状态不允许恢复时，
    # 回滚查询产生的当前事务，并保留原异常类型。
    except InvalidKnowledgeDocumentStateError:
        session.rollback()
        raise

    # 查询或者提交数据库失败。
    except (
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        session.rollback()

        raise KnowledgeManagementError(
            "知识文档恢复失败"
        ) from error