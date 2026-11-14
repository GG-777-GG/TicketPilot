"""
文件职责：在调用方事务中添加事件，或按明确目标只读查询历史。
作用与配合：由审计Service调用；写入转换为ORM并flush，查询不刷新或提交事务。
边界：不commit、不自行rollback，不判断角色，不修改或删除既有事件。
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.observability.context import get_request_id
from ticketpilot.observability.tracing import get_current_trace_id


def add_event(
    session: Session,
    event: AuditEventInput,
) -> AuditEventRecord:
    """添加一条事件，将提交和回滚决定留给业务Service。"""

    data = event.model_dump(mode="json")
    # 可信调用方显式提供的编号保留；其余读取当前请求的上下文。
    # 无HTTP上下文的脚本/测试仍可为空，不伪造关联编号。
    if event.request_id is None:
        request_id = get_request_id()
        data["request_id"] = str(request_id) if request_id is not None else None
    if event.trace_id is None:
        # 只读取有效当前Span；脚本或未开启追踪的入口仍可为空。
        data["trace_id"] = get_current_trace_id()

    record = AuditEventRecord(
        id=str(uuid4()),

        # JSON模式将UUID转成数据库String列需要的字符串。
        **data,
    )

    session.add(record)

    # 发送SQL并检查约束，当前事务仍未提交。
    session.flush()

    return record


def list_events(
    session: Session,
    *,
    approval_id: str | None,
    operation_id: str | None,
    limit: int,
    offset: int,
) -> list[AuditEventRecord]:
    """两个编号同时给出时取交集；同时间事件用编号保持确定排序。"""
    if approval_id is None and operation_id is None:
        raise ValueError("审计查询必须指定审批编号或操作编号")
    # Service多取一行判断has_more，因此仓储允许最多101行。
    if not 1 <= limit <= 101 or offset < 0:
        raise ValueError("审计分页参数无效")
    statement = select(AuditEventRecord)
    if approval_id is not None:
        statement = statement.where(AuditEventRecord.approval_id == approval_id)
    if operation_id is not None:
        statement = statement.where(AuditEventRecord.operation_id == operation_id)
    statement = statement.order_by(
        AuditEventRecord.occurred_at.asc(), AuditEventRecord.id.asc(),
    ).limit(limit).offset(offset)
    # 避免使用其他Session配置时，查询意外自动flush待保存业务对象。
    with session.no_autoflush:
        return list(session.scalars(statement))
