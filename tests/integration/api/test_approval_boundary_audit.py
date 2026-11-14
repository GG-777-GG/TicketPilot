"""
文件职责：验证HTTP角色拒绝、工作流外层失败与底层执行审计去重。
作用与配合：真实JWT、完整HTTP应用、正式图和业务Service，临时文件SQLite。
边界：不访问业务库或模型，不把401或认证前拒绝声明为已覆盖的审计事件。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.runtime import Runtime
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tests.factories.identity import build_user
from ticketpilot.app import create_app
from ticketpilot.approvals import repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest
from ticketpilot.audit import repository as audit_repository
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.core.config import settings
from ticketpilot.core.database import Base, get_session
from ticketpilot.identity.security import create_access_token
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord
from ticketpilot.workflows.ticket_closure import graph as closure, operations


pytestmark = pytest.mark.integration


class BoundaryContext(SimpleNamespace):
    """隐藏测试上下文详情，令牌只在发送请求时短暂生成。"""

    def __repr__(self):
        return "BoundaryContext(isolated_database=True)"


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    """每个请求和独立审计事务使用新Session及同一临时文件库。"""
    engine = create_engine("sqlite:///" + (tmp_path / "business.sqlite").as_posix(), hide_parameters=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        owner, manager, other = build_user(role="agent"), build_user(role="supervisor"), build_user(role="agent")
        ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com",
                              channel="web", subject="边界审计", description="测试", priority="medium", status="open")
        session.add_all([owner, manager, other, ticket])
        session.commit()
        record = service.create_approval(session=session, requester=owner,
                                         request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"))
        ids = owner.id, manager.id, other.id, ticket.id, record.id, record.operation_id
    application = create_app()
    path = tmp_path / "checkpoints" / "closure.sqlite"
    monkeypatch.setattr(settings, "approval_workflow_checkpoint_path", path)

    def request_session():
        """替换数据库依赖，认证及业务规则保持真实实现。"""
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = request_session
    try:
        with TestClient(application) as client:
            yield BoundaryContext(client=client, engine=engine, owner=ids[0], manager=ids[1], other=ids[2],
                                  ticket=ids[3], approval=ids[4], operation=ids[5], path=path,
                                  url=f"/v1/approvals/{ids[4]}")
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def headers(user_id):
    """测试JWT只用于请求，不放进fixture诊断输出。"""
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def failed_events(api):
    """从新Session读取已提交失败/拒绝事件。"""
    with Session(api.engine) as session:
        return list(session.scalars(select(AuditEventRecord).where(AuditEventRecord.outcome.in_(["failed", "denied"]))))


def run(api, action, user=None):
    """发送正式工作流POST。"""
    return api.client.post(api.url + "/workflow/run", headers=headers(user or api.manager), json={"action": action})


def start_and_approve(api):
    """真实HTTP准备已批准的暂停工作流。"""
    assert run(api, "start", api.owner).status_code == 200
    response = api.client.post(api.url + "/review", headers=headers(api.manager), json={"approved": True, "reason": "已核验"})
    assert response.status_code == 200


@pytest.mark.parametrize("endpoint", ["review", "execute"])
def test_http_role_denial_reaches_service_audit(boundary, endpoint):
    """已认证客服由Service拒绝，且只留下一个权限拒绝事件。"""
    api = boundary
    response = api.client.post(api.url + "/" + endpoint, headers=headers(api.owner),
                               json={"approved": True, "reason": "尝试审批"} if endpoint == "review" else None)
    assert response.status_code == 403
    saved, = failed_events(api)
    assert (saved.event_type, saved.outcome, saved.error_code) == ("approval.denied", "denied", "permission_denied")
    assert (saved.actor_id, saved.actor_role, saved.approval_id) == (api.owner, "agent", api.approval)
    assert saved.ticket_id is None and saved.operation_id is None


@pytest.mark.parametrize("endpoint", ["review", "execute"])
def test_missing_identity_still_returns_401_without_fabricated_actor(boundary, endpoint):
    """本批未增加认证失败审计，不伪造已认证身份。"""
    api = boundary
    response = api.client.post(api.url + "/" + endpoint,
                               json={"approved": True, "reason": "测试"} if endpoint == "review" else None)
    assert response.status_code == 401 and failed_events(api) == []


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_workflow_role_denial_is_audited_without_checkpoint_read(boundary, monkeypatch, action):
    """编排入口的角色拒绝不访问申请或检查点。"""
    api = boundary

    def unexpected_read(*args, **kwargs):
        """拒绝发生在查询之前。"""
        raise AssertionError("不应读取工作流")

    monkeypatch.setattr(operations, "get_workflow_status", unexpected_read)
    response = run(api, action, api.owner)
    assert response.status_code == 403 and not api.path.exists()
    saved, = failed_events(api)
    assert saved.outcome == "denied" and saved.actor_id == api.owner
    assert saved.ticket_id is None and saved.operation_id is None


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_workflow_state_conflict_has_verified_target_snapshot(boundary, action):
    """尚未启动的流程不能恢复或重试，但合法读取的目标可留存。"""
    api = boundary
    assert run(api, action).status_code == 409
    saved, = failed_events(api)
    assert (saved.error_code, saved.ticket_id, saved.operation_id) == ("state_conflict", api.ticket, api.operation)
    assert not api.path.exists()


@pytest.mark.parametrize("kind", ["missing", "foreign"])
def test_workflow_invisible_target_does_not_leak_binding(boundary, kind):
    """不存在和越权对象都返回404，审计也不补查内部目标。"""
    api = boundary
    target = str(uuid4()) if kind == "missing" else api.approval
    response = api.client.post(f"/v1/approvals/{target}/workflow/run",
                               headers=headers(api.manager if kind == "missing" else api.other), json={"action": "start"})
    assert response.status_code == 404
    saved, = failed_events(api)
    assert saved.error_code == "not_found" and saved.approval_id == target
    assert saved.ticket_id is None and saved.operation_id is None


def test_busy_workflow_records_conflict_without_releasing_other_holder(boundary):
    """失败请求不得释放其他调用持有的锁。"""
    api = boundary
    assert operations._operation_lock.acquire(blocking=False)
    try:
        assert run(api, "start").status_code == 409
        assert operations._operation_lock.locked()
    finally:
        operations._operation_lock.release()
    saved, = failed_events(api)
    assert saved.error_code == "state_conflict" and saved.operation_id is None


def test_checkpoint_io_failure_records_fixed_category_and_safe_response(boundary, monkeypatch):
    """检查点打开异常保存workflow_error，不向HTTP泄露原始正文。"""
    api = boundary

    def broken_open(*args, **kwargs):
        """模拟文件不可用。"""
        raise OSError("private-checkpoint-marker")

    monkeypatch.setattr(operations, "open_ticket_closure_workflow", broken_open)
    response = run(api, "start")
    assert response.status_code == 500 and "private-checkpoint-marker" not in response.text
    saved, = failed_events(api)
    assert saved.error_code == "workflow_error" and saved.operation_id == api.operation
    assert not operations._operation_lock.locked()


def test_unexpected_node_runtime_failure_is_safely_audited(boundary, monkeypatch):
    """节点运行时错误转换稳定500，留下外层失败事件。"""
    api = boundary

    def broken_read(*args, **kwargs):
        """在图中模拟不属于业务异常的运行时错误。"""
        raise RuntimeError("private-node-marker")

    monkeypatch.setattr(closure, "read_approval", broken_read)
    response = run(api, "start")
    assert response.status_code == 500 and "private-node-marker" not in response.text
    saved, = failed_events(api)
    assert saved.error_code == "workflow_error"


@pytest.mark.parametrize("kind", ["conflict", "storage"])
def test_node_execution_failure_is_not_audited_twice(boundary, monkeypatch, kind):
    """真实图调用执行Service，底层已记录的同一失败只产生一个事件。"""
    api = boundary
    start_and_approve(api)

    def fail_close(*args, **kwargs):
        """模拟执行条件冲突或数据库故障。"""
        if kind == "storage":
            raise SQLAlchemyError("执行数据库故障")
        return False

    monkeypatch.setattr(repository, "close_ticket_for_approval", fail_close)
    assert run(api, "resume").status_code == (409 if kind == "conflict" else 500)
    saved, = failed_events(api)
    assert saved.error_code == ("state_conflict" if kind == "conflict" else "storage_error")
    assert saved.operation_id == api.operation
    with Session(api.engine) as session:
        assert session.get(ApprovalRecord, api.approval).status == "approved"
        assert session.get(TicketRecord, api.ticket).status == "open"


def test_failed_lower_audit_attempt_is_not_retried_as_another_event(boundary, monkeypatch):
    """标记表示已尝试，底层审计数据库不可用也不能在外层重复发起。"""
    api = boundary
    start_and_approve(api)
    attempts = []

    def fail_close(*args, **kwargs):
        """触发底层条件冲突。"""
        return False

    def fail_audit(session, value):
        """统计失败审计尝试后模拟数据库故障。"""
        attempts.append(value)
        raise SQLAlchemyError("审计不可用")

    monkeypatch.setattr(repository, "close_ticket_for_approval", fail_close)
    monkeypatch.setattr(audit_repository, "add_event", fail_audit)
    assert run(api, "resume").status_code == 409
    assert len(attempts) == 1 and failed_events(api) == []


def test_runtime_error_after_committed_execution_does_not_undo_success(boundary, monkeypatch):
    """节点提交后的运行错误仍可留痕，但不能声称原业务已撤销。"""
    api = boundary
    start_and_approve(api)
    original = closure.execute_ticket_closure

    def fail_after_commit(state, runtime: Runtime[closure.TicketClosureContext]):
        """真实关闭并提交后再让节点报错。"""
        original(state, runtime)
        raise RuntimeError("测试节点提交后故障")

    monkeypatch.setattr(closure, "execute_ticket_closure", fail_after_commit)
    assert run(api, "resume").status_code == 500
    saved, = failed_events(api)
    assert saved.error_code == "workflow_error"
    with Session(api.engine) as session:
        assert session.get(ApprovalRecord, api.approval).status == "succeeded"
        assert session.get(TicketRecord, api.ticket).status == "closed"
        assert len(list(session.scalars(select(ApprovalExecutionRecord)))) == 1


def test_outer_audit_unavailable_preserves_original_http_error(boundary, monkeypatch):
    """外层审计不可用仍返回原角色403，不替换成审计500。"""
    api = boundary

    def fail_audit(*args, **kwargs):
        """模拟独立审计写入失败。"""
        raise SQLAlchemyError("审计不可用")

    monkeypatch.setattr(audit_repository, "add_event", fail_audit)
    assert run(api, "resume", api.owner).status_code == 403
    assert failed_events(api) == []


def test_readonly_graph_execution_guard_remains_permission_error(boundary):
    """图中的只申请执行范围拒绝属于权限错误，不能被OSError分支改成存储故障。"""
    api = boundary
    start_and_approve(api)
    with Session(api.engine, expire_on_commit=False) as session:
        manager = session.get(UserRecord, api.manager)
        with pytest.raises(service.ApprovalAccessError) as captured:
            operations.run_workflow(session=session, approval_id=api.approval, viewer=manager,
                                    checkpoint_path=api.path, action="resume", allow_execution=False)
    assert getattr(captured.value, "_failure_audit_attempted", False)
    saved, = failed_events(api)
    assert saved.outcome == "denied" and saved.error_code == "permission_denied"
    with Session(api.engine) as check:
        assert check.get(TicketRecord, api.ticket).status == "open"
        assert check.get(ApprovalRecord, api.approval).status == "approved"
