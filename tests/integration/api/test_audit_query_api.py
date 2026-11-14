"""
文件职责：验证管理审计查询的真实认证、过滤、分页、快照和只读边界。
作用与配合：完整应用和真实JWT，临时文件SQLite；另跑一条正式审批HTTP链路。
边界：不访问真实库/模型，不声明完整安全日志、只读查询留痕或防篡改。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tests.factories.identity import build_user
from ticketpilot.app import create_app
from ticketpilot.audit import repository, service
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.audit.schemas import AuditEventQuery
from ticketpilot.core.database import Base, get_session
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.security import create_access_token
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


class AuditQueryContext(SimpleNamespace):
    """不把测试令牌或用户内容放到诊断输出。"""

    def __repr__(self):
        return "AuditQueryContext(isolated_database=True)"


@pytest.fixture
def audit_api(tmp_path):
    """准备有相同时间戳的事件，检测过滤、稳定排序与分页边界。"""
    engine = create_engine("sqlite:///" + (tmp_path / "audit.sqlite").as_posix(), hide_parameters=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    approval, operation, other_approval, other_operation, ticket = [str(uuid4()) for _ in range(5)]
    with factory() as session:
        manager, actor = build_user(role="supervisor"), build_user(role="agent")
        session.add_all([manager, actor])
        session.commit()
        manager_id, actor_id = manager.id, actor.id
        timestamp = datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC)
        for number, kind in enumerate(["approval.requested", "approval.approved", "ticket.closed", "approval.denied", "approval.requested"], start=1):
            session.add(AuditEventRecord(
                id=f"00000000-0000-4000-8000-{number:012d}", event_type=kind,
                actor_id=actor_id, actor_role="agent", ticket_id=ticket,
                approval_id=other_approval if number == 5 else approval,
                operation_id=None if number == 4 else (other_operation if number == 5 else operation),
                outcome="denied" if number == 4 else "succeeded",
                error_code="permission_denied" if number == 4 else None,
                occurred_at=timestamp + timedelta(seconds=max(0, number - 2)),
            ))
        session.commit()
    application = create_app()

    def request_session():
        """每个HTTP请求使用新Session，身份验证保持真实实现。"""
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = request_session
    try:
        with TestClient(application) as client:
            yield AuditQueryContext(client=client, engine=engine, factory=factory, manager=manager_id,
                                    actor=actor_id, approval=approval, operation=operation,
                                    other_operation=other_operation, ticket=ticket)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def headers(user_id):
    """临时JWT仅用于请求，不持久化或写日志。"""
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def query(api, **params):
    """默认以真实管理身份请求只读接口。"""
    return api.client.get("/v1/audit-events", headers=headers(api.manager), params=params)


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("filter_name,count", [("approval_id", 4), ("operation_id", 3)])
def test_managers_read_only_matching_public_events(audit_api, role, filter_name, count):
    """两类管理角色能查询历史字段，不回传用户实体或任意正文。"""
    api = audit_api
    with api.factory() as session:
        session.get(UserRecord, api.manager).role = role
        session.commit()
    response = query(api, **{filter_name: api.approval if filter_name == "approval_id" else api.operation})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == count and payload["has_more"] is False
    assert payload["limit"] == 50 and payload["offset"] == 0
    for row in payload["items"]:
        assert row[filter_name] == (api.approval if filter_name == "approval_id" else api.operation)
        assert row["actor_id"] == api.actor and row["actor_role"] == "agent"
        assert row["request_id"] is None and row["trace_id"] is None
        assert {"password_hash", "access_token", "details", "raw_exception", "request_reason"}.isdisjoint(row)


def test_pagination_is_stable_with_same_timestamp_and_has_more(audit_api):
    """limit+1检测下一页；同时间按事件编号排序，尾页和空页不误报。"""
    api = audit_api
    first = query(api, approval_id=api.approval, limit=2).json()
    second = query(api, approval_id=api.approval, limit=2, offset=2).json()
    empty = query(api, approval_id=api.approval, limit=2, offset=4).json()
    assert first["has_more"] is True and second["has_more"] is False
    assert [row["id"][-12:] for row in first["items"]] == ["000000000001", "000000000002"]
    assert [row["id"][-12:] for row in second["items"]] == ["000000000003", "000000000004"]
    assert second["offset"] == 2 and empty["items"] == [] and empty["has_more"] is False


@pytest.mark.parametrize("params", [
    {"approval_id": "invalid"}, {"operation_id": "invalid"},
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "invalid"},
])
def test_malformed_query_returns_422(audit_api, params):
    """非法UUID及越界分页在HTTP解析阶段拒绝。"""
    api = audit_api
    values = {"approval_id": api.approval, **params}
    assert query(api, **values).status_code == 422


@pytest.mark.parametrize("matches", [True, False])
def test_two_filters_use_intersection(audit_api, matches):
    """两个编号共同约束，不能以OR扩大查询范围。"""
    api = audit_api
    response = query(api, approval_id=api.approval, operation_id=api.operation if matches else api.other_operation)
    assert response.status_code == 200
    assert len(response.json()["items"]) == (3 if matches else 0)


@pytest.mark.parametrize("role", ["agent", "unknown"])
def test_disallowed_role_never_queries_audit_repository(audit_api, monkeypatch, role):
    """客户端不能通过猜测编号越过角色检查。"""
    api = audit_api
    with api.factory() as session:
        session.get(UserRecord, api.manager).role = role
        session.commit()

    def unexpected_query(*args, **kwargs):
        """权限拒绝前不读取审计事件。"""
        raise AssertionError("不允许的角色不能查询")

    monkeypatch.setattr(repository, "list_events", unexpected_query)
    assert query(api, approval_id=api.approval).status_code == 403


def test_disabled_manager_is_rejected_by_authentication(audit_api):
    """角色合法也不能绕过停用状态检查。"""
    api = audit_api
    with api.factory() as session:
        session.get(UserRecord, api.manager).is_active = False
        session.commit()
    assert query(api, approval_id=api.approval).status_code == 403


@pytest.mark.parametrize("kind", ["missing", "invalid"])
def test_authentication_is_required(audit_api, kind):
    """缺失和无效令牌保持401，不泄露事件数据。"""
    response = audit_api.client.get("/v1/audit-events", params={"approval_id": audit_api.approval},
                                    headers={} if kind == "missing" else {"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_query_requires_at_least_one_target(audit_api):
    """不开放无目标的整表浏览。"""
    assert query(audit_api).status_code == 422


def test_unknown_target_returns_empty_page(audit_api):
    """没有事件时返回空页，不要求原审批对象存在。"""
    response = query(audit_api, approval_id=str(uuid4()))
    assert response.status_code == 200 and response.json()["items"] == []


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_no_public_mutation_endpoint(audit_api, method):
    """此路由只注册GET，不允许客户端新增或篡改事件。"""
    response = audit_api.client.request(method, "/v1/audit-events", headers=headers(audit_api.manager),
                                        params={"approval_id": audit_api.approval})
    assert response.status_code == 405


def test_query_issues_no_mutating_sql_or_self_audit(audit_api):
    """真实认证和查询全过程无INSERT/UPDATE/DELETE，避免查询产生噪声。"""
    api = audit_api
    statements = []

    def track(connection, cursor, statement, parameters, context, executemany):
        """仅记录SQL首词，不收集可能含敏感数据的参数。"""
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(api.engine, "before_cursor_execute", track)
    assert query(api, approval_id=api.approval).status_code == 200
    assert {"INSERT", "UPDATE", "DELETE"}.isdisjoint(statements)


def test_history_is_visible_after_actor_deleted_and_role_changed(audit_api):
    """审计读取快照，原用户生命周期不会重写历史角色。"""
    api = audit_api
    with api.factory() as session:
        session.get(UserRecord, api.actor).role = "admin"
        session.commit()
        session.delete(session.get(UserRecord, api.actor))
        session.commit()
    payload = query(api, approval_id=api.approval).json()
    assert len(payload["items"]) == 4
    assert all(row["actor_id"] == api.actor and row["actor_role"] == "agent" for row in payload["items"])


def test_storage_error_has_safe_response(audit_api, monkeypatch):
    """数据库故障不以空页伪装成功，也不输出原始异常正文。"""
    def broken_query(*args, **kwargs):
        """模拟数据库查询失败。"""
        raise SQLAlchemyError("private-query-marker")

    monkeypatch.setattr(repository, "list_events", broken_query)
    response = query(audit_api, approval_id=audit_api.approval)
    assert response.status_code == 500
    assert response.json() == {"detail": "审计查询服务暂时不可用"}


@pytest.mark.parametrize("role,active", [("agent", True), ("supervisor", False), ("unknown", True)])
def test_direct_service_call_also_enforces_permissions(audit_api, monkeypatch, role, active):
    """绕过HTTP直接调用Service也不能提升查询权限。"""
    api = audit_api

    def unexpected_query(*args, **kwargs):
        """权限检查必须先于仓储读取。"""
        raise AssertionError("不允许读取审计")

    monkeypatch.setattr(repository, "list_events", unexpected_query)
    with api.factory() as session:
        viewer = session.get(UserRecord, api.manager)
        viewer.role, viewer.is_active = role, active
        with pytest.raises(service.AuditAccessError):
            service.query_events(session=session, viewer=viewer, query=AuditEventQuery(approval_id=api.approval))


def test_real_approval_lifecycle_is_queryable_via_http(audit_api):
    """正式创建/审批/执行/重放生成事件，再用查询接口读回同一操作链。"""
    api = audit_api
    with api.factory() as session:
        ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com",
                              channel="web", subject="查询验收", description="测试", priority="medium", status="open")
        session.add(ticket)
        session.commit()
        ticket_id = ticket.id
    response = api.client.post("/v1/approvals", headers=headers(api.actor),
                               json={"ticket_id": ticket_id, "request_reason": "申请关闭"})
    assert response.status_code == 201
    record = response.json()
    base = f"/v1/approvals/{record['id']}"
    assert api.client.post(base + "/review", headers=headers(api.manager), json={"approved": True, "reason": "已核验"}).status_code == 200
    for _ in range(2):
        assert api.client.post(base + "/execute", headers=headers(api.manager)).status_code == 200
    response = query(api, operation_id=record["operation_id"])
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(rows) == 4
    assert {row["event_type"] for row in rows} == {"approval.requested", "approval.approved", "ticket.closed", "execution.replayed"}
    assert all(row["approval_id"] == record["id"] and row["ticket_id"] == ticket_id for row in rows)
