"""
文件职责：独立保存失败尝试，并为管理角色提供受限只读查询。

作用与配合：
- 调用方先结束原业务事务，再调用本模块。
- 调用方提供可信的事件和数据库Session工厂。
- 本模块复用审计Repository保存事件。

边界：
- 不修改工单或审批状态。
- 不负责识别用户身份或决定业务错误类别。
- 数据库写入失败返回False，不替代原业务异常。
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ticketpilot.audit import repository
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.audit.schemas import AuditEventPage, AuditEventQuery, AuditEventResponse
from ticketpilot.identity.models import UserRecord

logger = logging.getLogger(__name__)


def record_failed_attempt(
    *,
    event: AuditEventInput,
    session_factory: sessionmaker[Session],
) -> bool:
    """用独立事务保存失败尝试，返回是否成功保存。"""

    # 防止调用方误把成功事件放进独立事务，
    # 破坏成功审计与业务共同提交的约定。
    if event.outcome not in ("denied", "failed"):
        raise ValueError("此接口只接受失败或无权限事件")

    # 失败原因使用固定类别，不保存原始异常正文。
    if event.error_code is None:
        raise ValueError("失败审计必须提供错误类别")

    try:
        # 创建新Session并开启事务：
        # 正常退出时提交，发生异常时回滚，最后关闭Session。
        with session_factory.begin() as audit_session:
            repository.add_event(
                audit_session,
                event,
            )

        # 离开with后事务已经成功提交，才能返回True。
        return True

    except SQLAlchemyError:
        # 不输出原始异常、SQL参数、令牌或敏感正文。
        # 调用方仍应保留并抛出原来的业务错误。
        logger.error("失败审计未能保存")
        return False


class AuditAccessError(PermissionError):
    """当前身份不允许查看审计历史。"""


class AuditQueryError(ValueError):
    """查询缺少明确的业务目标。"""


class AuditStorageError(RuntimeError):
    """审计查询数据库操作失败。"""


def query_events(*, session: Session, viewer: UserRecord, query: AuditEventQuery) -> AuditEventPage:
    """只允许活跃管理角色按目标查看历史快照，不提交或补查业务记录。"""
    if not viewer.is_active or viewer.role not in ("supervisor", "admin"):
        raise AuditAccessError("只有主管或管理员可以查看审计记录")
    if query.approval_id is None and query.operation_id is None:
        raise AuditQueryError("请提供审批编号或操作编号")
    try:
        rows = repository.list_events(
            session,
            approval_id=str(query.approval_id) if query.approval_id is not None else None,
            operation_id=str(query.operation_id) if query.operation_id is not None else None,
            limit=query.limit + 1, offset=query.offset,
        )
    except SQLAlchemyError as error:
        session.rollback()
        raise AuditStorageError("查询审计记录失败") from error
    return AuditEventPage(
        items=[AuditEventResponse.model_validate(row, from_attributes=True) for row in rows[:query.limit]],
        limit=query.limit, offset=query.offset, has_more=len(rows) > query.limit,
    )
