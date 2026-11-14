"""
文件职责：验证工作流进度查询HTTP的权限、文件读取、状态和错误响应。
作用与配合：使用真实JWT、正式图和Service，只替换业务库与检查点路径。
边界：全部写入临时SQLite，不访问真实工单、项目检查点或模型服务。
"""

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.runtime import Runtime
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.workflows.ticket_closure import graph as closure, service as closure_service
from ticketpilot.workflows.ticket_closure import operations
from ticketpilot.workflows.ticket_closure.checkpoints import open_ticket_closure_workflow
from ticketpilot.app import create_app
from ticketpilot.approvals import repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest, ApprovalReviewRequest
from ticketpilot.core.config import settings
from ticketpilot.core.database import get_session
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.security import create_access_token
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


class WorkflowApiContext(SimpleNamespace):
    """保存测试上下文，失败输出中不展示测试JWT。"""

    def __repr__(self):
        return "WorkflowApiContext(isolated_database=True)"


@pytest.fixture
def workflow_api(database_session: Session, tmp_path, monkeypatch):
    """每次HTTP使用独立Session，认证及业务查询均走真实实现。"""
    owner, manager = build_user(role="agent"), build_user(role="supervisor")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="test@example.com",
        channel="web", subject="工作流查询", description="问题已解决", priority="medium", status="open",
    )
    database_session.add_all([owner, manager, ticket])
    database_session.commit()
    record = service.create_approval(
        session=database_session,
        request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"), requester=owner,
    )
    path = tmp_path / "checkpoints" / "workflow.sqlite"
    monkeypatch.setattr(settings, "approval_workflow_checkpoint_path", path)
    application = create_app()

    def request_session():
        """同一测试库内，每个HTTP请求都使用新的业务会话。"""
        with Session(bind=database_session.get_bind(), autoflush=False, expire_on_commit=False) as session:
            yield session

    application.dependency_overrides[get_session] = request_session
    try:
        with TestClient(application) as client:
            yield WorkflowApiContext(
                client=client, session=database_session, owner=owner, manager=manager,
                ticket=ticket, record=record, path=path,
                url=f"/v1/approvals/{record.id}/workflow",
                config={"configurable": {"thread_id": f"ticket-closure:{record.operation_id}"}},
            )
    finally:
        application.dependency_overrides.clear()


def headers_for(user):
    """临时JWT只用于请求，不放进上下文的诊断输出。"""
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def business_snapshot(api):
    """按全部列核对业务记录，检测查询是否意外修改数据。"""
    return {
        model.__tablename__: [dict(row) for row in api.session.execute(select(model.__table__)).mappings()]
        for model in (TicketRecord, ApprovalRecord, ApprovalExecutionRecord)
    }


def start_pending(api):
    """使用正式图准备待审批检查点，保持测试关注GET查询。"""
    with open_ticket_closure_workflow(api.path) as graph:
        with Session(bind=api.session.get_bind(), expire_on_commit=False) as session:
            result = graph.invoke(
                {"approval_id": api.record.id, "operation_id": api.record.operation_id,
                 "ticket_id": api.ticket.id, "approval_status": None, "result": ""},
                config=api.config,
                context=closure.TicketClosureContext(session=session, viewer=session.get(UserRecord, api.owner.id)),
            )
    assert result["__interrupt__"]


def review(api, approved=True):
    """通过正式审核Service准备业务状态，保留独立请求可见性。"""
    service.review_approval(
        session=api.session, approval_id=api.record.id,
        request=ApprovalReviewRequest(approved=approved, reason="查询接口验收"), reviewer=api.manager,
    )


def resume(api):
    """用独立Session和管理用户完成测试流程。"""
    with open_ticket_closure_workflow(api.path) as graph:
        with Session(bind=api.session.get_bind(), expire_on_commit=False) as session:
            return graph.invoke(
                Command(resume={"recheck": True}), config=api.config,
                context=closure.TicketClosureContext(session=session, viewer=session.get(UserRecord, api.manager.id)),
            )


def track_checkpoint_reads(monkeypatch):
    """记录检查点是否被访问，验证权限判断在读取之前。"""
    calls = []
    original = closure_service.open_ticket_closure_workflow

    def tracked(*args, **kwargs):
        """记录访问并继续使用真实文件保存器。"""
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(closure_service, "open_ticket_closure_workflow", tracked)
    return calls


def test_query_unstarted_does_not_create_file(workflow_api):
    """没有检查点时返回未启动和实时审批信息，不创建目录或文件。"""
    api = workflow_api
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 200
    body = response.json()
    assert body["approval"]["id"] == api.record.id and body["approval"]["status"] == "pending"
    assert body["thread_id"] == f"ticket-closure:{api.record.operation_id}"
    assert body["started"] is False and body["checkpoint_status"] is None
    assert body["next_nodes"] == [] and not body["waiting_for_approval"] and not body["has_error"]
    assert not api.path.exists() and not api.path.parent.exists()
    assert "password_hash" not in response.text
    assert business_snapshot(api) == before


def test_existing_file_without_target_thread_returns_unstarted(workflow_api):
    """文件中只有其他会话时，不能误认为当前流程已经启动。"""
    api = workflow_api
    with open_ticket_closure_workflow(api.path) as graph:
        assert graph.get_state({"configurable": {"thread_id": "another-thread"}}).values == {}
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 200 and response.json()["started"] is False


@pytest.mark.parametrize("role, own, expected", [
    ("agent", True, 200), ("agent", False, 404),
    ("supervisor", True, 200), ("supervisor", False, 200),
    ("admin", True, 200), ("admin", False, 200),
])
def test_query_scope_checked_before_file_read(workflow_api, monkeypatch, role, own, expected):
    """有实际检查点时仍先检查申请可见性，越权不读文件。"""
    api = workflow_api
    start_pending(api)
    viewer = api.owner if own else build_user(role=role)
    viewer.role = role
    api.session.add(viewer)
    api.session.commit()
    calls = track_checkpoint_reads(monkeypatch)
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(viewer))
    assert response.status_code == expected
    assert len(calls) == (1 if expected == 200 else 0)
    if expected == 200:
        assert response.json()["approval"]["requested_by"] == api.owner.id
        assert response.json()["started"] is True
    else:
        assert response.json() == {"detail": "审批申请不存在"}
    assert business_snapshot(api) == before


@pytest.mark.parametrize("credentials", ["missing", "invalid", "unknown_user"])
def test_query_requires_real_authentication(workflow_api, monkeypatch, credentials):
    """认证失败先返回401，不访问检查点。"""
    api = workflow_api
    start_pending(api)
    headers = {}
    if credentials == "invalid":
        headers = {"Authorization": "Bearer invalid-token"}
    elif credentials == "unknown_user":
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    calls = track_checkpoint_reads(monkeypatch)
    response = api.client.get(api.url, headers=headers)
    assert response.status_code == 401 and not calls
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("change", ["disabled", "unknown_role"])
def test_query_rejects_disallowed_account(workflow_api, monkeypatch, change):
    """账号停用或未知角色均返回403，旧检查点不能提供访问许可。"""
    api = workflow_api
    start_pending(api)
    if change == "disabled":
        api.owner.is_active = False
    else:
        api.owner.role = "unknown"
    api.session.commit()
    calls = track_checkpoint_reads(monkeypatch)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 403 and not calls


@pytest.mark.parametrize("approval_id, expected", [("invalid-id", 422), (str(uuid4()), 404)])
def test_query_invalid_or_missing_id(workflow_api, monkeypatch, approval_id, expected):
    """非法编号和不存在的申请不触及检查点文件。"""
    api = workflow_api
    start_pending(api)
    calls = track_checkpoint_reads(monkeypatch)
    response = api.client.get(f"/v1/approvals/{approval_id}/workflow", headers=headers_for(api.owner))
    assert response.status_code == expected and not calls


@pytest.mark.parametrize("phase", ["pending", "approved", "rejected", "succeeded"])
def test_query_reports_progress_without_executing(workflow_api, phase):
    """GET仅读取：获批但未恢复仍等待，完成和拒绝分别返回各自状态。"""
    api = workflow_api
    start_pending(api)
    if phase != "pending":
        review(api, approved=phase != "rejected")
    if phase in ("rejected", "succeeded"):
        resume(api)
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 200
    body = response.json()
    assert body["approval"]["status"] == phase
    assert body["checkpoint_status"] == ("pending" if phase == "approved" else phase)
    assert body["started"] is True and body["has_error"] is False
    waiting = phase in ("pending", "approved")
    assert body["waiting_for_approval"] is waiting
    assert body["next_nodes"] == (["wait_for_approval"] if waiting else [])
    assert business_snapshot(api) == before
    if phase == "approved":
        assert before["tickets"][0]["status"] == "open" and not before["approval_executions"]


@pytest.mark.parametrize("field", ["approval_id", "operation_id", "ticket_id"])
def test_query_rejects_checkpoint_binding_mismatch(workflow_api, field):
    """检查点里的申请、操作或目标不匹配时返回409，不公开其内容。"""
    api = workflow_api
    start_pending(api)
    with open_ticket_closure_workflow(api.path) as graph:
        graph.update_state(api.config, {field: str(uuid4())})
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 409
    assert response.json() == {"detail": "工作流检查点与审批申请不一致"}
    assert business_snapshot(api) == before


@pytest.mark.parametrize("failure", ["corrupt_file", "os_error"])
def test_query_file_error_returns_safe_500(workflow_api, monkeypatch, failure):
    """无效SQLite或文件访问失败统一返回安全提示。"""
    api = workflow_api
    if failure == "corrupt_file":
        api.path.parent.mkdir()
        api.path.write_bytes(b"not a sqlite database")
    else:
        start_pending(api)

        def fail_open(*args, **kwargs):
            """模拟内部路径访问错误，确认不会泄漏原始异常。"""
            raise OSError("internal-filesystem-marker")

        monkeypatch.setattr(closure_service, "open_ticket_closure_workflow", fail_open)
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 500
    assert response.json() == {"detail": "工作流查询服务暂时不可用"}
    assert business_snapshot(api) == before


def test_query_business_storage_error_prevents_file_read(workflow_api, monkeypatch):
    """审批查询本身失败，HTTP映射500且不继续读取检查点。"""
    api = workflow_api
    start_pending(api)
    calls = track_checkpoint_reads(monkeypatch)

    def fail_query(*args, **kwargs):
        """模拟审批存储故障。"""
        raise SQLAlchemyError("internal-database-marker")

    monkeypatch.setattr(repository, "get_approval_by_id", fail_query)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 500 and not calls
    assert response.json() == {"detail": "工作流查询服务暂时不可用"}


def test_query_failed_checkpoint_preserves_current_business_status(workflow_api, monkeypatch):
    """提交后节点失败时分别展示实时状态和旧检查点，隐藏原始错误。"""
    api = workflow_api
    review(api)
    real_execute = closure.execute_ticket_closure

    def fail_after_commit(state, runtime: Runtime[closure.TicketClosureContext]):
        """实际关闭并提交后，再让图节点报错。"""
        real_execute(state, runtime)
        raise RuntimeError("internal-node-error-marker")

    monkeypatch.setattr(closure, "execute_ticket_closure", fail_after_commit)
    with open_ticket_closure_workflow(api.path) as graph:
        with Session(bind=api.session.get_bind(), expire_on_commit=False) as session:
            with pytest.raises(RuntimeError, match="internal-node-error-marker"):
                graph.invoke(
                    {"approval_id": api.record.id, "operation_id": api.record.operation_id,
                     "ticket_id": api.ticket.id, "approval_status": None, "result": ""},
                    config=api.config,
                    context=closure.TicketClosureContext(session=session, viewer=session.get(UserRecord, api.manager.id)),
                )
    before = business_snapshot(api)
    response = api.client.get(api.url, headers=headers_for(api.owner))
    assert response.status_code == 200
    body = response.json()
    assert body["approval"]["status"] == "succeeded" and body["checkpoint_status"] == "approved"
    assert body["has_error"] is True and body["waiting_for_approval"] is False
    assert body["next_nodes"] == ["execute_ticket_closure"]
    assert "internal-node-error-marker" not in response.text
    assert business_snapshot(api) == before


def test_client_query_parameters_cannot_redirect_checkpoint(workflow_api):
    """客户端自填thread_id、文件路径和用户编号不改变后端定位与权限。"""
    api = workflow_api
    start_pending(api)
    other_path = api.path.parent / "client-selected.sqlite"
    response = api.client.get(
        api.url, headers=headers_for(api.owner),
        params={"thread_id": "another-thread", "checkpoint_path": str(other_path), "viewer_id": api.manager.id},
    )
    assert response.status_code == 200
    assert response.json()["thread_id"] == f"ticket-closure:{api.record.operation_id}"
    assert response.json()["started"] is True and not other_path.exists()


def run_action(api, action, user=None):
    """调用真实POST编排入口，默认使用普通申请人。"""
    return api.client.post(api.url + "/run", headers=headers_for(user or api.owner), json={"action": action})


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_run_starts_pending_for_allowed_owner(workflow_api, role):
    """三种角色可启动本人pending申请，启动仅进入等待、不关闭工单。"""
    api = workflow_api
    api.owner.role = role
    api.session.commit()
    before = business_snapshot(api)
    response = run_action(api, "start")
    assert response.status_code == 200
    body = response.json()
    assert body["started"] and body["waiting_for_approval"] and not body["has_error"]
    assert body["next_nodes"] == ["wait_for_approval"]
    assert body["approval"]["status"] == body["checkpoint_status"] == "pending"
    assert body["thread_id"] == api.config["configurable"]["thread_id"]
    assert business_snapshot(api) == before


def test_duplicate_start_preserves_checkpoint(workflow_api):
    """重复start返回原进度，不产生新的检查点或重置节点。"""
    api = workflow_api
    first = run_action(api, "start")
    assert first.status_code == 200
    with open_ticket_closure_workflow(api.path) as graph:
        checkpoint_id = graph.get_state(api.config).config["configurable"]["checkpoint_id"]
    second = run_action(api, "start")
    assert second.status_code == 200 and second.json() == first.json()
    with open_ticket_closure_workflow(api.path) as graph:
        assert graph.get_state(api.config).config["configurable"]["checkpoint_id"] == checkpoint_id


@pytest.mark.parametrize("status", ["approved", "rejected", "executing", "succeeded", "failed"])
def test_new_run_requires_pending(workflow_api, status):
    """已有业务状态不能通过start新建流程；尚无文件时拒绝也不创建。"""
    api = workflow_api
    api.record.status = status
    api.session.commit()
    before = business_snapshot(api)
    response = run_action(api, "start", api.manager)
    assert response.status_code == 409 and not api.path.exists()
    assert business_snapshot(api) == before


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_run_requires_existing_workflow(workflow_api, action):
    """管理用户也不能恢复或重试不存在的流程。"""
    api = workflow_api
    response = run_action(api, action, api.manager)
    assert response.status_code == 409 and not api.path.exists()


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_run_agent_cannot_resume_or_retry(workflow_api, action):
    """普通申请人可查看和启动，但恢复/重试不得获得管理执行权限。"""
    api = workflow_api
    assert run_action(api, "start").status_code == 200
    before = business_snapshot(api)
    assert run_action(api, action).status_code == 403
    assert business_snapshot(api) == before


def test_resume_without_decision_waits_again(workflow_api):
    """唤醒未审核申请仍暂停，resume不会替用户提交审批决定。"""
    api = workflow_api
    assert run_action(api, "start").status_code == 200
    before = business_snapshot(api)
    response = run_action(api, "resume", api.manager)
    assert response.status_code == 200 and response.json()["waiting_for_approval"]
    assert business_snapshot(api) == before


@pytest.mark.parametrize("approved", [True, False])
def test_full_http_workflow_lifecycle(workflow_api, approved):
    """只通过HTTP启动、审核、恢复和查询，核对实际执行记录。"""
    api = workflow_api
    assert run_action(api, "start").status_code == 200
    reviewed = api.client.post(
        f"/v1/approvals/{api.record.id}/review", headers=headers_for(api.manager),
        json={"approved": approved, "reason": "HTTP闭环验收"},
    )
    assert reviewed.status_code == 200
    response = run_action(api, "resume", api.manager)
    assert response.status_code == 200
    body = response.json()
    expected = "succeeded" if approved else "rejected"
    assert body["approval"]["status"] == body["checkpoint_status"] == expected
    assert body["next_nodes"] == [] and not body["has_error"]
    stored = business_snapshot(api)
    assert stored["tickets"][0]["status"] == ("closed" if approved else "open")
    assert len(stored["approval_executions"]) == int(approved)
    if approved:
        receipt = stored["approval_executions"][0]
        assert receipt["operation_id"] == api.record.operation_id
        assert receipt["executed_by"] == api.manager.id
        assert receipt["result"] == body["approval"]["result"]
    assert api.client.get(api.url, headers=headers_for(api.owner)).json() == body
    assert run_action(api, "start").json() == body
    for action in ("resume", "retry"):
        assert run_action(api, action, api.manager).status_code == 409
    assert business_snapshot(api) == stored


@pytest.mark.parametrize("payload", [{}, {"action": "unknown"}, {"action": True},
    {"action": "start", "approved": True}, {"action": "start", "thread_id": "forged"},
    {"action": "start", "ticket_id": str(uuid4())}])
def test_run_rejects_invalid_or_forged_payload(workflow_api, payload):
    """Pydantic拒绝非法动作和客户端自报的业务字段。"""
    api = workflow_api
    response = api.client.post(api.url + "/run", headers=headers_for(api.owner), json=payload)
    assert response.status_code == 422 and not api.path.exists()


@pytest.mark.parametrize("kind", ["missing_token", "invalid_token", "disabled", "unknown_role", "other_agent", "missing_id", "invalid_id"])
def test_run_enforces_authentication_and_scope(workflow_api, kind):
    """POST仍走真实认证与申请范围，拒绝请求不创建文件。"""
    api = workflow_api
    headers = headers_for(api.owner)
    url = api.url + "/run"
    expected = 403
    if kind == "missing_token":
        headers, expected = {}, 401
    elif kind == "invalid_token":
        headers, expected = {"Authorization": "Bearer invalid"}, 401
    elif kind == "disabled":
        api.owner.is_active = False
    elif kind == "unknown_role":
        api.owner.role = "unknown"
    elif kind == "other_agent":
        other = build_user(role="agent")
        api.session.add(other)
        api.session.commit()
        headers, expected = headers_for(other), 404
    else:
        identifier = str(uuid4()) if kind == "missing_id" else "invalid-id"
        url, expected = f"/v1/approvals/{identifier}/workflow/run", 404 if kind == "missing_id" else 422
    api.session.commit()
    response = api.client.post(url, headers=headers, json={"action": "start"})
    assert response.status_code == expected and not api.path.exists()


def test_retry_while_waiting_is_conflict(workflow_api):
    """等待审批应使用resume，不能用retry跳过暂停点。"""
    api = workflow_api
    assert run_action(api, "start").status_code == 200
    assert run_action(api, "retry", api.manager).status_code == 409
    assert run_action(api, "resume", api.manager).status_code == 200


@pytest.mark.parametrize("after_commit", [False, True])
def test_run_failure_retry_preserves_operation(workflow_api, monkeypatch, after_commit):
    """提交前回滚和提交后报错都能通过HTTP重试，唯一操作保持不变。"""
    api = workflow_api
    assert run_action(api, "start").status_code == 200
    review(api)
    real_close = repository.close_ticket_for_approval
    real_execute = closure.execute_ticket_closure
    calls = []

    def counted_close(*args, **kwargs):
        """统计实际关闭尝试；提交后的重试不应再次调用。"""
        calls.append(True)
        result = real_close(*args, **kwargs)
        if not after_commit:
            raise SQLAlchemyError("internal-before-commit-marker")
        return result

    def fail_after_commit(state, runtime):
        """真实执行已提交，随后模拟节点返回前异常。"""
        real_execute(state, runtime)
        raise service.ApprovalStorageError("internal-after-commit-marker")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "close_ticket_for_approval", counted_close)
        if after_commit:
            patch.setattr(closure, "execute_ticket_closure", fail_after_commit)
        failed = run_action(api, "resume", api.manager)
    assert failed.status_code == 500
    assert failed.json() == {"detail": "工作流运行服务暂时不可用"}
    stored = business_snapshot(api)
    assert stored["tickets"][0]["status"] == ("closed" if after_commit else "open")
    assert len(stored["approval_executions"]) == int(after_commit)
    status = api.client.get(api.url, headers=headers_for(api.owner)).json()
    assert status["has_error"] and status["next_nodes"] == ["execute_ticket_closure"]
    assert run_action(api, "resume", api.manager).status_code == 409
    # 用统计替身保留计数，但不再制造错误。
    def retry_count(*args, **kwargs):
        """记录恢复后的新关闭尝试。"""
        calls.append(True)
        return real_close(*args, **kwargs)
    monkeypatch.setattr(repository, "close_ticket_for_approval", retry_count)
    retried = run_action(api, "retry", api.manager)
    assert retried.status_code == 200 and not retried.json()["has_error"]
    final = business_snapshot(api)
    assert len(final["approval_executions"]) == 1
    assert final["approval_executions"][0]["operation_id"] == api.record.operation_id
    assert len(calls) == (1 if after_commit else 2)
    if after_commit:
        assert final == stored


def test_busy_run_rejects_second_request_and_releases_lock(workflow_api, monkeypatch):
    """真实并发HTTP请求中，一项持锁运行，另一项立即冲突，随后可继续查询。"""
    api = workflow_api
    entered, release = Event(), Event()
    original = operations.get_workflow_status

    def pause_first(*args, **kwargs):
        """在持锁后暂停第一项，制造确定的同时到达。"""
        if not entered.is_set():
            entered.set()
            assert release.wait(5), "测试屏障等待超时"
        return original(*args, **kwargs)

    monkeypatch.setattr(operations, "get_workflow_status", pause_first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_action, api, "start")
        try:
            assert entered.wait(5)
            second = run_action(api, "start")
            assert second.status_code == 409
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    assert run_action(api, "start").status_code == 200


def test_run_file_failure_releases_lock(workflow_api, monkeypatch):
    """文件连接失败返回安全500，finally释放锁后下一次启动成功。"""
    api = workflow_api
    def fail_open(*args, **kwargs):
        """模拟创建文件保存器时失败。"""
        raise OSError("internal-path-marker")
    with monkeypatch.context() as patch:
        patch.setattr(operations, "open_ticket_closure_workflow", fail_open)
        response = run_action(api, "start")
    assert response.status_code == 500
    assert response.json() == {"detail": "工作流运行服务暂时不可用"}
    assert run_action(api, "start").status_code == 200


def test_run_retry_failed_read_node(workflow_api, monkeypatch):
    """启动时读取节点失败，规范初始编号使HTTP可查询并从原节点重试。"""
    api = workflow_api
    def fail_read(state, runtime):
        """模拟初始读取节点存储异常，不修改业务记录。"""
        raise service.ApprovalStorageError("internal-read-marker")
    with monkeypatch.context() as patch:
        patch.setattr(closure, "read_approval", fail_read)
        response = run_action(api, "start")
    assert response.status_code == 500
    status = api.client.get(api.url, headers=headers_for(api.owner))
    assert status.status_code == 200 and status.json()["has_error"]
    assert status.json()["next_nodes"] == ["read_approval"]
    retried = run_action(api, "retry", api.manager)
    assert retried.status_code == 200 and not retried.json()["has_error"]
    assert retried.json()["waiting_for_approval"]
    assert business_snapshot(api)["approval_executions"] == []
