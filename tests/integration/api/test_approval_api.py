"""
文件职责：验证审批接口的认证、事务、错误响应及普通状态接口的关闭限制。
作用与配合：使用完整应用、真实 JWT 验证及审批 Service/Repository，
仅替换数据库为测试数据库，每个HTTP请求使用独立Session，
不替换当前用户认证依赖。
边界：不访问真实工单库或模型；测试令牌仅在内存中使用。
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.app import create_app
from ticketpilot.approvals import repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalReviewRequest
from ticketpilot.core.database import get_session
from ticketpilot.identity.security import create_access_token
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


class ApprovalApiContext(SimpleNamespace):
    """保存测试上下文，诊断输出时隐藏内存中的测试令牌。"""

    def __repr__(self) -> str:
        return "ApprovalApiContext(test_database=True)"


@pytest.fixture
def approval_api(database_session: Session):
    """创建独立应用与已提交父记录，通过真实 Bearer 认证发送请求。"""

    user = build_user(role="agent")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="customer@example.com",
        channel="web", subject="申请关闭", description="问题已解决", priority="medium", status="open",
    )
    database_session.add_all([user, ticket])
    database_session.commit()
    application = create_app()

    def test_session():
        """连接当前测试库，但像实际请求一样单独创建和关闭Session。"""
        with Session(
            bind=database_session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ) as request_session:
            yield request_session

    application.dependency_overrides[get_session] = test_session
    try:
        with TestClient(application) as client:
            yield ApprovalApiContext(
                client=client, user=user, ticket=ticket, session=database_session,
                headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
                payload={"ticket_id": ticket.id, "request_reason": "  客户问题已解决  "},
            )
    finally:
        application.dependency_overrides.clear()


def assert_no_approval(api, expected_status: str = "open") -> None:
    """失败请求不应留下申请，工单也仍处于原状态。"""
    assert api.session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
    api.session.expire_all()
    assert api.session.get(TicketRecord, api.ticket.id).status == expected_status


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_create_approval_returns_201_and_persists_pending(approval_api, role: str) -> None:
    """允许角色通过真实认证创建申请；返回身份来自登录用户。"""
    api = approval_api
    api.user.role = role
    api.session.commit()
    response = api.client.post("/v1/approvals", headers=api.headers, json=api.payload)
    assert response.status_code == 201
    payload = response.json()
    assert UUID(payload["id"]) != UUID(payload["operation_id"])
    assert payload["ticket_id"] == api.ticket.id
    assert payload["requested_by"] == api.user.id
    assert payload["status"] == "pending" and payload["action"] == "close_ticket"
    assert payload["request_reason"] == "客户问题已解决"
    for field in ("reviewed_by", "approval_reason", "reviewed_at", "result"):
        assert payload[field] is None
    assert payload["created_at"] and payload["updated_at"]
    api.session.expire_all()
    stored = api.session.get(ApprovalRecord, payload["id"])
    assert stored is not None and stored.requested_by == api.user.id
    assert api.session.get(TicketRecord, api.ticket.id).status == "open"
    assert "password_hash" not in payload


@pytest.mark.parametrize("credentials", ["missing", "invalid", "unknown_user"])
def test_unauthenticated_request_returns_401(approval_api, credentials: str) -> None:
    """缺少、无效令牌及令牌指向不存在用户时，都不能创建申请。"""
    api = approval_api
    headers = {}
    if credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "unknown_user":
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    response = api.client.post("/v1/approvals", headers=headers, json=api.payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_no_approval(api)


@pytest.mark.parametrize("role, active", [("agent", False), ("unknown", True)])
def test_disallowed_account_returns_403(approval_api, role: str, active: bool) -> None:
    """停用账号由认证拒绝，未知角色由业务规则拒绝。"""
    api = approval_api
    api.user.role, api.user.is_active = role, active
    api.session.commit()
    response = api.client.post("/v1/approvals", headers=api.headers, json=api.payload)
    assert response.status_code == 403
    assert_no_approval(api)


def test_missing_ticket_returns_404(approval_api) -> None:
    """合法 UUID 但目标不存在时返回业务 404。"""
    api = approval_api
    response = api.client.post("/v1/approvals", headers=api.headers, json={**api.payload, "ticket_id": str(uuid4())})
    assert response.status_code == 404
    assert response.json()["detail"] == "工单不存在"
    assert_no_approval(api)


def test_closed_ticket_returns_409(approval_api) -> None:
    """已关闭工单不再创建新申请。"""
    api = approval_api
    api.ticket.status = "closed"
    api.session.commit()
    response = api.client.post("/v1/approvals", headers=api.headers, json=api.payload)
    assert response.status_code == 409
    assert_no_approval(api, expected_status="closed")


@pytest.mark.parametrize("changes", [
    {"ticket_id": "not-a-uuid"}, {"request_reason": "   "},
    {"action": "delete_ticket"}, {"requested_by": str(uuid4())},
    {"reviewed_by": str(uuid4())}, {"status": "succeeded"},
])
def test_invalid_or_forged_fields_return_422(approval_api, changes: dict) -> None:
    """请求校验拒绝非法值及客户端冒填的身份、状态。"""
    api = approval_api
    response = api.client.post("/v1/approvals", headers=api.headers, json={**api.payload, **changes})
    assert response.status_code == 422
    assert_no_approval(api)


def test_storage_failure_returns_safe_500_and_rolls_back(approval_api, monkeypatch: pytest.MonkeyPatch) -> None:
    """实际 flush 后模拟故障，检查 HTTP 提示及事务回滚。"""
    api = approval_api
    original_add = repository.add_approval

    def fail_after_flush(session, record):
        """保留实际 INSERT，随后触发存储错误。"""
        original_add(session, record)
        raise SQLAlchemyError("模拟内部数据库错误")

    monkeypatch.setattr(repository, "add_approval", fail_after_flush)
    response = api.client.post("/v1/approvals", headers=api.headers, json=api.payload)
    assert response.status_code == 500
    assert response.json() == {"detail": "审批服务暂时不可用"}
    assert_no_approval(api)


@pytest.fixture
def query_approval(approval_api) -> ApprovalRecord:
    """创建他人的申请；不通过创建接口耦合查询测试。"""
    api = approval_api
    owner = build_user(role="agent")
    api.session.add(owner)
    api.session.flush()
    record = ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=api.ticket.id, requested_by=owner.id, request_reason="申请关闭")
    api.session.add(record)
    api.session.commit()
    return record


@pytest.mark.parametrize("role, own, expected", [
    ("agent", True, 200), ("agent", False, 404),
    ("supervisor", False, 200), ("admin", False, 200),
])
def test_query_uses_authenticated_user_scope(approval_api, query_approval: ApprovalRecord, role: str, own: bool, expected: int) -> None:
    """真实JWT与SQL查询共同验证对象级查询范围。"""
    api = approval_api
    api.user.role = role
    if own:
        query_approval.requested_by = api.user.id
    api.session.commit()
    response = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers)
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["id"] == query_approval.id
        assert response.json()["operation_id"] == query_approval.operation_id
        assert response.json()["status"] == "pending"
    else:
        assert response.json() == {"detail": "审批申请不存在"}


def test_other_owner_and_missing_record_have_identical_404(approval_api, query_approval: ApprovalRecord) -> None:
    """越权与不存在的响应相同，查询参数也不能扩大本人范围。"""
    api = approval_api
    forbidden = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers)
    missing = api.client.get(f"/v1/approvals/{uuid4()}", headers=api.headers)
    forged = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers, params={"requested_by": query_approval.requested_by})
    assert forbidden.status_code == missing.status_code == forged.status_code == 404
    assert forbidden.json() == missing.json() == forged.json() == {"detail": "审批申请不存在"}


@pytest.mark.parametrize("credentials", ["missing", "invalid", "unknown_user"])
def test_query_requires_valid_authentication(approval_api, query_approval: ApprovalRecord, credentials: str) -> None:
    """详情也必须经过认证，不能仅凭申请编号访问。"""
    api = approval_api
    headers = {}
    if credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "unknown_user":
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    response = api.client.get(f"/v1/approvals/{query_approval.id}", headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize("role, active", [("agent", False), ("unknown", True)])
def test_query_rejects_disabled_or_unknown_role(approval_api, query_approval: ApprovalRecord, role: str, active: bool) -> None:
    """停用用户和未知角色均返回403。"""
    api = approval_api
    api.user.role, api.user.is_active = role, active
    api.session.commit()
    response = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers)
    assert response.status_code == 403


def test_query_rejects_malformed_id(approval_api) -> None:
    """格式错误的路径参数返回422。"""
    response = approval_api.client.get("/v1/approvals/not-a-uuid", headers=approval_api.headers)
    assert response.status_code == 422


def test_query_storage_error_returns_safe_500(approval_api, query_approval: ApprovalRecord, monkeypatch) -> None:
    """数据库故障不向HTTP响应暴露内部信息。"""
    api = approval_api

    def fail(*_args, **_kwargs):
        """模拟查询错误。"""
        raise SQLAlchemyError("内部查询故障")

    monkeypatch.setattr(repository, "get_approval_by_id", fail)
    response = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers)
    assert response.status_code == 500
    assert response.json() == {"detail": "审批服务暂时不可用"}
    assert api.session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("approved", [True, False])
def test_review_returns_200_and_records_real_reviewer(approval_api, query_approval: ApprovalRecord, role: str, approved: bool) -> None:
    """通过真实角色依赖提交决定，只改变申请、不关闭工单。"""
    api = approval_api
    api.user.role = role
    api.session.commit()
    operation_id = query_approval.operation_id
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": approved, "reason": "  已核验  "})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ("approved" if approved else "rejected")
    assert body["reviewed_by"] == api.user.id and body["approval_reason"] == "已核验"
    assert body["reviewed_at"] and body["operation_id"] == operation_id
    assert body["result"] is None
    api.session.expire_all()
    assert api.session.get(ApprovalRecord, query_approval.id).status == body["status"]
    assert api.session.get(TicketRecord, api.ticket.id).status == "open"


@pytest.mark.parametrize("role, active", [("agent", True), ("unknown", True), ("supervisor", False)])
def test_review_requires_active_management_role(approval_api, query_approval: ApprovalRecord, role: str, active: bool) -> None:
    """客服、未知角色及停用主管都不能审批。"""
    api = approval_api
    api.user.role, api.user.is_active = role, active
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": True, "reason": "理由"})
    assert response.status_code == 403
    api.session.refresh(query_approval)
    assert query_approval.status == "pending" and query_approval.reviewed_by is None


@pytest.mark.parametrize("credentials", ["missing", "invalid", "unknown_user"])
def test_review_requires_valid_token(approval_api, query_approval: ApprovalRecord, credentials: str) -> None:
    """审批端点同样要求有效登录身份。"""
    api = approval_api
    headers = {}
    if credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "unknown_user":
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=headers, json={"approved": True, "reason": "理由"})
    assert response.status_code == 401


def test_review_missing_record_returns_404(approval_api) -> None:
    """管理角色提交不存在申请时返回404。"""
    api = approval_api
    api.user.role = "supervisor"
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{uuid4()}/review", headers=api.headers, json={"approved": True, "reason": "理由"})
    assert response.status_code == 404


@pytest.mark.parametrize("changes", [
    {"approved": "true"}, {"approved": None}, {"reason": "   "},
    {"reviewed_by": str(uuid4())}, {"status": "succeeded"},
])
def test_review_invalid_or_forged_fields_return_422(approval_api, query_approval: ApprovalRecord, changes: dict) -> None:
    """严格决定类型、理由和服务端字段不能被客户端绕过。"""
    api = approval_api
    api.user.role = "admin"
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": True, "reason": "理由", **changes})
    assert response.status_code == 422
    api.session.refresh(query_approval)
    assert query_approval.status == "pending" and query_approval.reviewed_by is None


def test_review_malformed_id_returns_422(approval_api) -> None:
    """格式错误的申请编号不能进入业务写入。"""
    api = approval_api
    api.user.role = "admin"
    api.session.commit()
    response = api.client.post("/v1/approvals/not-a-uuid/review", headers=api.headers, json={"approved": True, "reason": "理由"})
    assert response.status_code == 422


@pytest.mark.parametrize("previous_status", ["approved", "rejected", "executing", "succeeded", "failed"])
def test_review_non_pending_record_returns_409(approval_api, query_approval: ApprovalRecord, previous_status: str) -> None:
    """所有非pending状态都不能再次提交决定。"""
    api = approval_api
    api.user.role = "admin"
    query_approval.status = previous_status
    query_approval.approval_reason = "原理由"
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": True, "reason": "覆盖理由"})
    assert response.status_code == 409
    api.session.refresh(query_approval)
    assert query_approval.status == previous_status
    assert query_approval.approval_reason == "原理由"


def test_second_reviewer_cannot_overwrite_first_decision(approval_api, query_approval: ApprovalRecord) -> None:
    """第二个审批人的相反决定返回409，原身份、时间及理由不变。"""
    api = approval_api
    api.user.role = "supervisor"
    another = build_user(role="admin")
    api.session.add(another)
    api.session.commit()
    first = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": True, "reason": "第一决定"})
    assert first.status_code == 200
    second = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers={"Authorization": f"Bearer {create_access_token(another.id)}"}, json={"approved": False, "reason": "第二决定"})
    assert second.status_code == 409
    saved = api.client.get(f"/v1/approvals/{query_approval.id}", headers=api.headers)
    assert saved.json() == first.json()


def test_review_storage_failure_rolls_back_and_returns_safe_500(approval_api, query_approval: ApprovalRecord, monkeypatch) -> None:
    """条件更新之后发生错误，审批字段回滚且HTTP不暴露内部信息。"""
    api = approval_api
    api.user.role = "admin"
    api.session.commit()
    original_update = repository.review_pending_approval

    def fail_after_update(*args, **kwargs):
        """先真实更新，再模拟数据库异常。"""
        original_update(*args, **kwargs)
        raise SQLAlchemyError("内部审批故障")

    monkeypatch.setattr(repository, "review_pending_approval", fail_after_update)
    response = api.client.post(f"/v1/approvals/{query_approval.id}/review", headers=api.headers, json={"approved": True, "reason": "理由"})
    assert response.status_code == 500
    assert response.json() == {"detail": "审批服务暂时不可用"}
    api.session.refresh(query_approval)
    assert query_approval.status == "pending"
    assert query_approval.reviewed_by is None and query_approval.approval_reason is None


@pytest.fixture
def approved_approval(approval_api, query_approval: ApprovalRecord) -> ApprovalRecord:
    """通过真实审批服务准备已批准记录，HTTP测试集中验证执行入口。"""
    api = approval_api
    api.user.role = "supervisor"
    api.session.commit()
    return service.review_approval(session=api.session, approval_id=query_approval.id, request=ApprovalReviewRequest(approved=True, reason="已核验"), reviewer=api.user)


@pytest.mark.parametrize("role", ["supervisor", "admin"])
def test_execute_returns_success_and_closes_ticket(approval_api, approved_approval: ApprovalRecord, role: str) -> None:
    """允许角色执行后，HTTP响应、申请、工单和结果记录一致。"""
    api = approval_api
    api.user.role = role
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=api.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded" and body["operation_id"] == approved_approval.operation_id
    api.session.expire_all()
    receipt = api.session.get(ApprovalExecutionRecord, body["operation_id"])
    assert receipt.result == body["result"] and receipt.executed_by == api.user.id
    assert api.session.get(TicketRecord, api.ticket.id).status == "closed"


def test_http_creation_review_execution_and_replay(approval_api) -> None:
    """完整HTTP闭环：创建、批准、执行、重放、查询工单与结果记录。"""
    api = approval_api
    api.user.role = "admin"
    api.session.commit()
    created = api.client.post("/v1/approvals", headers=api.headers, json=api.payload)
    assert created.status_code == 201
    approval_id, operation_id = created.json()["id"], created.json()["operation_id"]
    reviewed = api.client.post(f"/v1/approvals/{approval_id}/review", headers=api.headers, json={"approved": True, "reason": "允许关闭"})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "approved"
    # 即使已有批准记录，也不能用普通状态接口代替审批执行。
    bypass = api.client.patch(f"/v1/tickets/{api.ticket.id}/status", headers=api.headers, json={"status": "closed"})
    assert bypass.status_code == 409
    before_execute = api.client.get(f"/v1/tickets/{api.ticket.id}", headers=api.headers)
    assert before_execute.status_code == 200 and before_execute.json()["status"] == "open"
    api.session.expire_all()
    assert api.session.get(ApprovalRecord, approval_id).status == "approved"
    assert api.session.scalar(select(func.count()).select_from(ApprovalExecutionRecord)) == 0
    first = api.client.post(f"/v1/approvals/{approval_id}/execute", headers=api.headers)
    replay = api.client.post(f"/v1/approvals/{approval_id}/execute", headers=api.headers)
    assert first.status_code == replay.status_code == 200
    for field in ("id", "operation_id", "status", "result", "reviewed_by", "approval_reason"):
        assert first.json()[field] == replay.json()[field]
    detail = api.client.get(f"/v1/approvals/{approval_id}", headers=api.headers)
    ticket = api.client.get(f"/v1/tickets/{api.ticket.id}", headers=api.headers)
    assert detail.status_code == ticket.status_code == 200
    assert detail.json()["status"] == "succeeded" and ticket.json()["status"] == "closed"
    assert api.session.get(ApprovalExecutionRecord, operation_id).result == first.json()["result"]
    assert api.session.scalar(select(func.count()).select_from(ApprovalExecutionRecord)) == 1


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
@pytest.mark.parametrize("previous_status", ["open", "in_progress", "resolved", "closed"])
def test_direct_close_returns_409_without_writing(approval_api, role: str, previous_status: str) -> None:
    """所有角色及原状态均不能直接关闭；拒绝后工单和审批记录不变。"""
    api = approval_api
    api.user.role = role
    api.ticket.status = previous_status
    api.session.commit()
    path = f"/v1/tickets/{api.ticket.id}"
    before = api.client.get(path, headers=api.headers)
    assert before.status_code == 200

    response = api.client.patch(f"{path}/status", headers=api.headers, json={"status": "closed"})

    assert response.status_code == 409
    assert response.json() == {"detail": "关闭工单需要提交审批申请，并在获批后执行"}
    after = api.client.get(path, headers=api.headers)
    assert after.status_code == 200 and after.json() == before.json()
    assert_no_approval(api, expected_status=previous_status)
    assert api.session.scalar(select(func.count()).select_from(ApprovalExecutionRecord)) == 0


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
@pytest.mark.parametrize("next_status", ["open", "in_progress", "resolved"])
def test_normal_status_update_still_persists(approval_api, role: str, next_status: str) -> None:
    """关闭限制不影响三种普通目标状态，独立请求能够读取保存结果。"""
    api = approval_api
    api.user.role = role
    api.ticket.status = "in_progress" if next_status == "open" else "open"
    api.session.commit()
    path = f"/v1/tickets/{api.ticket.id}"

    response = api.client.patch(f"{path}/status", headers=api.headers, json={"status": next_status})

    assert response.status_code == 200 and response.json()["status"] == next_status
    detail = api.client.get(path, headers=api.headers)
    assert detail.status_code == 200 and detail.json()["status"] == next_status
    assert_no_approval(api, expected_status=next_status)
    assert api.session.scalar(select(func.count()).select_from(ApprovalExecutionRecord)) == 0


def test_direct_close_missing_ticket_returns_404(approval_api) -> None:
    """先查询资源，直接关闭不存在的工单仍返回404。"""
    api = approval_api
    response = api.client.patch(f"/v1/tickets/{uuid4()}/status", headers=api.headers, json={"status": "closed"})
    assert response.status_code == 404 and response.json() == {"detail": "工单不存在"}
    assert_no_approval(api)


@pytest.mark.parametrize("credentials", ["missing", "invalid"])
def test_direct_close_requires_authentication(approval_api, credentials: str) -> None:
    """关闭业务判断不代替认证，无令牌或无效令牌仍先返回401。"""
    api = approval_api
    headers = {} if credentials == "missing" else {"Authorization": "Bearer invalid-token"}
    response = api.client.patch(f"/v1/tickets/{api.ticket.id}/status", headers=headers, json={"status": "closed"})
    assert response.status_code == 401
    assert_no_approval(api)


@pytest.mark.parametrize("role, active", [("agent", True), ("unknown", True), ("supervisor", False)])
def test_execute_rejects_disallowed_accounts(approval_api, approved_approval: ApprovalRecord, role: str, active: bool) -> None:
    """执行入口要求有效管理角色，不仅依赖已有审批结果。"""
    api = approval_api
    api.user.role, api.user.is_active = role, active
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=api.headers)
    assert response.status_code == 403
    api.session.refresh(approved_approval)
    assert approved_approval.status == "approved"
    assert api.session.get(ApprovalExecutionRecord, approved_approval.operation_id) is None


@pytest.mark.parametrize("credentials", ["missing", "invalid", "unknown_user"])
def test_execute_requires_valid_token(approval_api, approved_approval: ApprovalRecord, credentials: str) -> None:
    """无法认证的请求不能进入执行事务。"""
    api = approval_api
    headers = {}
    if credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "unknown_user":
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    response = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize("status", ["pending", "rejected", "executing", "failed", "succeeded"])
def test_execute_rejects_invalid_state_without_receipt(approval_api, approved_approval: ApprovalRecord, status: str) -> None:
    """未批准和缺失成功凭据的状态均返回409，不产生执行结果。"""
    api = approval_api
    approved_approval.status = status
    api.session.commit()
    response = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=api.headers)
    assert response.status_code == 409
    api.session.refresh(approved_approval)
    assert approved_approval.status == status
    assert api.session.get(ApprovalExecutionRecord, approved_approval.operation_id) is None


@pytest.mark.parametrize("approval_id, expected", [("not-a-uuid", 422), (str(uuid4()), 404)])
def test_execute_rejects_invalid_or_missing_target(approval_api, approved_approval: ApprovalRecord, approval_id: str, expected: int) -> None:
    """目标只能来自有效且实际存在的审批申请编号。"""
    response = approval_api.client.post(f"/v1/approvals/{approval_id}/execute", headers=approval_api.headers)
    assert response.status_code == expected


@pytest.mark.parametrize("failure_point", ["after_close", "after_receipt"])
def test_execute_database_failure_returns_safe_500_and_can_retry(approval_api, approved_approval: ApprovalRecord, monkeypatch, failure_point: str) -> None:
    """执行中的故障返回固定提示，三处回滚后同一申请能正常重试。"""
    api = approval_api
    target = "close_ticket_for_approval" if failure_point == "after_close" else "add_execution"
    original = getattr(repository, target)

    def fail(*args, **kwargs):
        """实际执行数据库修改后再制造故障。"""
        original(*args, **kwargs)
        raise SQLAlchemyError("内部执行故障")

    with monkeypatch.context() as patch:
        patch.setattr(repository, target, fail)
        response = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=api.headers)
    assert response.status_code == 500
    assert response.json() == {"detail": "审批执行服务暂时不可用"}
    api.session.refresh(approved_approval)
    api.session.refresh(api.ticket)
    assert approved_approval.status == "approved" and api.ticket.status == "open"
    assert api.session.get(ApprovalExecutionRecord, approved_approval.operation_id) is None
    retry = api.client.post(f"/v1/approvals/{approved_approval.id}/execute", headers=api.headers)
    assert retry.status_code == 200 and retry.json()["status"] == "succeeded"
