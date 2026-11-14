"""
文件职责：接收审批 HTTP 请求，调用业务服务并返回响应。
作用与配合：复用身份模块的 CurrentUser、数据库 Session，
以及审批 Service 和请求/响应模型；由 app.py 注册。
边界：不直接写数据库，审批与工单执行事务交给业务服务。
"""

from fastapi import APIRouter, HTTPException, status
from uuid import UUID
from ticketpilot.workflows.ticket_closure import service as closure_service
from ticketpilot.workflows.ticket_closure import operations as closure_operations
from ticketpilot.approvals import execution, service
from ticketpilot.approvals.models import ApprovalRecord
from ticketpilot.approvals.schemas import (
    ApprovalCreateRequest,
    ApprovalResponse,
    ApprovalReviewRequest,
    ApprovalWorkflowResponse,
    ApprovalWorkflowRunRequest,
)
from ticketpilot.core.config import settings
from ticketpilot.core.dependencies import DatabaseSession
from ticketpilot.identity.dependencies import (
    CurrentUser,
)


router = APIRouter(
    prefix="/v1/approvals",
    tags=["approvals"],
)


def to_approval_response(
    record: ApprovalRecord,
) -> ApprovalResponse:
    """读取 ORM 对象的属性，转换成公开响应。"""

    return ApprovalResponse.model_validate(
        record,
        from_attributes=True,
    )


@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_approval(
    request: ApprovalCreateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalResponse:
    """为当前登录用户创建一项待审批申请。"""

    try:
        record = service.create_approval(
            session=session,
            request=request,
            # 身份来自认证依赖，不读取客户端自报的身份。
            requester=current_user,
        )

    except service.ApprovalAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    except service.ApprovalTicketNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except service.ApprovalConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="审批服务暂时不可用",
        ) from error

    return to_approval_response(record)

@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
)
def get_approval(
    approval_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalResponse:
    """查看当前登录用户有权访问的审批申请。"""

    try:
        record = service.get_approval(
            session=session,
            approval_id=str(approval_id),
            viewer=current_user,
        )

    except service.ApprovalAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    except service.ApprovalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="审批服务暂时不可用",
        ) from error

    return to_approval_response(record)


@router.get(
    "/{approval_id}/workflow",
    response_model=ApprovalWorkflowResponse,
)
def get_approval_workflow(
    approval_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalWorkflowResponse:
    """按当前用户权限查询审批实时数据和工作流检查点。"""

    try:
        return closure_service.get_workflow_status(
            session=session,
            approval_id=str(approval_id),
            viewer=current_user,
            checkpoint_path=settings.approval_workflow_checkpoint_path,
        )
    except service.ApprovalAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except service.ApprovalNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except service.ApprovalConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工作流查询服务暂时不可用",
        ) from error


@router.post(
    "/{approval_id}/workflow/run",
    response_model=ApprovalWorkflowResponse,
)
def run_approval_workflow(
    approval_id: UUID,
    request: ApprovalWorkflowRunRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalWorkflowResponse:
    """启动、恢复或重试流程；身份与检查点路径均由后端提供。"""

    try:
        return closure_operations.run_workflow(
            session=session, approval_id=str(approval_id), viewer=current_user,
            checkpoint_path=settings.approval_workflow_checkpoint_path, action=request.action,
        )
    except service.ApprovalAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except service.ApprovalNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except service.ApprovalConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工作流运行服务暂时不可用",
        ) from error


@router.post(
    "/{approval_id}/review",
    response_model=ApprovalResponse,
)
def review_approval(
    approval_id: UUID,
    request: ApprovalReviewRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalResponse:
    """先认证身份，再由Service检查角色并审计被拒绝的尝试。"""

    try:
        record = service.review_approval(
            session=session,
            approval_id=str(approval_id),
            request=request,
            reviewer=current_user,
        )

    except service.ApprovalAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    except service.ApprovalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except service.ApprovalConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="审批服务暂时不可用",
        ) from error

    return to_approval_response(record)


@router.post(
    "/{approval_id}/execute",
    response_model=ApprovalResponse,
)
def execute_approval(
    approval_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ApprovalResponse:
    """由Service检查执行权限并审计；目标和编号只从已保存申请取得。"""

    try:
        record = execution.execute_approval(
            session=session,
            approval_id=str(approval_id),
            executor=current_user,
        )
    except service.ApprovalAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except service.ApprovalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except service.ApprovalConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except service.ApprovalStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="审批执行服务暂时不可用",
        ) from error

    return to_approval_response(record)
