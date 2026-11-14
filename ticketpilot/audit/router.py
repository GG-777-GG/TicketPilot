"""
文件职责：提供管理角色只读审计查询HTTP入口。
作用与配合：复用真实认证、数据库依赖及审计查询Service，由app.py装配。
边界：不提供公开新增/修改/删除事件接口，不扩大普通客服权限。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ticketpilot.audit import service
from ticketpilot.audit.schemas import AuditEventPage, AuditEventQuery
from ticketpilot.core.dependencies import DatabaseSession
from ticketpilot.identity.dependencies import CurrentUser


router = APIRouter(prefix="/v1/audit-events", tags=["audit"])


@router.get("", response_model=AuditEventPage)
def get_audit_events(
    session: DatabaseSession,
    current_user: CurrentUser,
    approval_id: UUID | None = None,
    operation_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventPage:
    """指定至少一个目标编号；两者同时提供则必须同时匹配。"""
    try:
        return service.query_events(
            session=session, viewer=current_user,
            query=AuditEventQuery(approval_id=approval_id, operation_id=operation_id, limit=limit, offset=offset),
        )
    except service.AuditAccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except service.AuditQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except service.AuditStorageError as error:
        raise HTTPException(status_code=500, detail="审计查询服务暂时不可用") from error
