"""
文件职责：验证真实HTTP请求编号、同步路由上下文、并发隔离和审计关联。
作用与配合：完整应用、真实JWT与临时文件数据库，测试路由仅挂在测试应用。
边界：不访问业务库/模型，不宣称已有真实Trace或跨进程上下文传播。
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tests.factories.identity import build_user
from ticketpilot.app import create_app
from ticketpilot.approvals import repository as approval_repository
from ticketpilot.audit import repository
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.core.database import Base, get_session
from ticketpilot.identity.security import create_access_token
from ticketpilot.observability.context import get_request_id, request_context
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


class CorrelationContext(SimpleNamespace):
    """测试诊断不显示任何令牌。"""

    def __repr__(self):
        return "CorrelationContext(isolated_database=True)"


@pytest.fixture
def correlation(tmp_path):
    """准备真实应用及隔离数据库，普通身份用于成功和拒绝审计。"""
    engine = create_engine("sqlite:///" + (tmp_path / "business.sqlite").as_posix(), hide_parameters=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        user = build_user(role="agent")
        ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com",
                              channel="web", subject="请求编号", description="测试", priority="medium", status="open")
        session.add_all([user, ticket])
        session.commit()
        user_id, ticket_id = user.id, ticket.id
    app = create_app()

    def session_dependency():
        """每个请求使用新会话，独立失败审计连接同一临时数据库。"""
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency

    @app.get("/test/sync-context")
    def sync_context():
        """真实同步路由在线程池中读取编号。"""
        return {"request_id": str(get_request_id())}

    @app.get("/test/async-context")
    async def async_context():
        """异步路由读取同一请求上下文。"""
        return {"request_id": str(get_request_id())}

    @app.get("/test/handled/{code}")
    def handled(code: int):
        """模拟已经转换的业务HTTP错误。"""
        raise HTTPException(status_code=code, detail="测试业务错误")

    @app.get("/test/unhandled")
    def unhandled():
        """模拟未捕获异常，外层500必须带同一编号并隐藏正文。"""
        raise RuntimeError("private-unhandled-marker")

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield CorrelationContext(app=app, client=client, engine=engine, factory=factory, user=user_id, ticket=ticket_id)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def headers(api):
    """临时JWT只在请求时生成。"""
    return {"Authorization": f"Bearer {create_access_token(api.user)}"}


@pytest.mark.parametrize("path", ["/test/sync-context", "/test/async-context"])
def test_request_header_matches_route_context(correlation, path):
    """同步/异步路由取得与响应头相同的合法UUID。"""
    response = correlation.client.get(path)
    assert response.status_code == 200
    assert UUID(response.headers["X-Request-ID"])
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert get_request_id() is None


@pytest.mark.parametrize("supplied", [None, "untrusted-header", "00000000-0000-4000-8000-000000000001"])
def test_each_request_uses_fresh_server_identifier(correlation, supplied):
    """服务器不采用客户端自报编号，也不复用上个请求编号。"""
    values = {} if supplied is None else {"X-Request-ID": supplied}
    first = correlation.client.get("/health", headers=values)
    second = correlation.client.get("/health", headers=values)
    one, two = first.headers["X-Request-ID"], second.headers["X-Request-ID"]
    assert UUID(one) and UUID(two) and one != two and one != supplied
    assert first.json() == {"status": "ok", "service": "TicketPilot"}


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_handled_error_keeps_status_body_and_request_header(correlation, code):
    """已经处理的HTTP错误不被中间件改变，只附加定位编号。"""
    response = correlation.client.get(f"/test/handled/{code}")
    assert response.status_code == code and response.json() == {"detail": "测试业务错误"}
    assert UUID(response.headers["X-Request-ID"])


def test_unhandled_500_has_original_request_id_and_safe_body(correlation, caplog):
    """外层异常处理从request.state取编号，不能在已恢复的ContextVar中找。"""
    response = correlation.client.get("/test/unhandled")
    assert response.status_code == 500 and response.json() == {"detail": "服务暂时不可用"}
    identifier = response.headers["X-Request-ID"]
    assert UUID(identifier) and get_request_id() is None
    records = [item for item in caplog.records if item.getMessage() == "请求处理失败"]
    assert len(records) == 1 and records[0].request_id == identifier
    assert records[0].trace_id == response.headers["X-Trace-ID"]
    assert "private-unhandled-marker" not in response.text


def test_overlapping_http_requests_do_not_mix_identifiers(correlation):
    """两个同步路由用屏障保证重叠，测试真实线程池上下文传播。"""
    barrier = Barrier(2, timeout=10)

    @correlation.app.get("/test/overlap")
    def overlap():
        """保存等待前后的编号，暴露任何串号。"""
        before = get_request_id()
        barrier.wait()
        return {"before": str(before), "after": str(get_request_id())}

    def call():
        """并发发送真实HTTP请求。"""
        return correlation.client.get("/test/overlap")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result(timeout=15) for future in [pool.submit(call), pool.submit(call)]]
    identifiers = []
    for response in (first, second):
        assert response.status_code == 200
        identifier = response.headers["X-Request-ID"]
        assert response.json() == {"before": identifier, "after": identifier}
        identifiers.append(identifier)
    assert identifiers[0] != identifiers[1] and get_request_id() is None


def test_success_and_denied_audit_match_their_own_response(correlation):
    """成功业务事务和独立权限拒绝审计分别关联各自HTTP请求。"""
    api = correlation
    created = api.client.post("/v1/approvals", headers=headers(api), json={"ticket_id": api.ticket, "request_reason": "申请关闭"})
    assert created.status_code == 201
    approval_id = created.json()["id"]
    denied = api.client.post(f"/v1/approvals/{approval_id}/review", headers=headers(api), json={"approved": True, "reason": "尝试审批"})
    assert denied.status_code == 403
    with Session(api.engine) as check:
        rows = list(check.scalars(select(AuditEventRecord)))
        assert len(rows) == 2
        requested, = [row for row in rows if row.event_type == "approval.requested"]
        rejected, = [row for row in rows if row.event_type == "approval.denied"]
        assert requested.request_id == created.headers["X-Request-ID"]
        assert rejected.request_id == denied.headers["X-Request-ID"]
        assert requested.request_id != rejected.request_id
        assert requested.trace_id == created.headers["X-Trace-ID"]
        assert rejected.trace_id == denied.headers["X-Trace-ID"]
        assert requested.trace_id != rejected.trace_id


def test_storage_failure_audit_keeps_id_after_rollback(correlation, monkeypatch):
    """原业务回滚不清除请求上下文，新Session仍能读取同一编号。"""
    api = correlation

    def fail_query(*args, **kwargs):
        """模拟创建申请的数据库错误。"""
        raise SQLAlchemyError("测试业务故障")

    monkeypatch.setattr(approval_repository, "get_ticket_for_approval", fail_query)
    response = api.client.post("/v1/approvals", headers=headers(api), json={"ticket_id": api.ticket, "request_reason": "申请关闭"})
    assert response.status_code == 500
    with Session(api.engine) as check:
        failed, = list(check.scalars(select(AuditEventRecord)))
        assert failed.event_type == "approval.failed" and failed.request_id == response.headers["X-Request-ID"]
        assert failed.trace_id == response.headers["X-Trace-ID"]


def test_trusted_explicit_event_id_is_not_replaced(correlation):
    """后端显式提供的关联编号保持不变，无HTTP脚本也可主动关联。"""
    explicit = uuid4()
    with correlation.factory() as session:
        with request_context() as current:
            assert explicit != current
            saved = repository.add_event(session, AuditEventInput(event_type="approval.failed", outcome="failed", error_code="storage_error", request_id=explicit))
            session.commit()
            identifier = saved.id
    with Session(correlation.engine) as check:
        assert check.get(AuditEventRecord, identifier).request_id == str(explicit)
