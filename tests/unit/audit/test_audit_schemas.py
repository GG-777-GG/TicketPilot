"""
文件职责：验证审计输入白名单、标识格式及序列化。
作用与配合：直接构造后端事件模型，不连接数据库。
边界：仅验证结构约束，不代表已实现业务事件语义和身份认证。
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ticketpilot.audit.schemas import AuditEventInput


def test_minimal_event_allows_unavailable_identity_and_trace():
    """无法确认身份和未接Trace时可留空，结果与事件本身必须提供。"""
    event = AuditEventInput(event_type="approval.denied", outcome="denied", error_code="permission_denied")
    assert event.actor_id is None and event.actor_role is None and event.trace_id is None


def test_json_dump_serializes_identifiers_for_string_columns():
    """UUID序列化为字符串，供Repository写入已有String列。"""
    identifiers = {name: uuid4() for name in ("actor_id", "approval_id", "ticket_id", "operation_id", "request_id")}
    event = AuditEventInput(event_type="ticket.closed", outcome="succeeded", actor_role="supervisor",
                           trace_id="a" * 32, **identifiers)
    data = event.model_dump(mode="json")
    for name, value in identifiers.items():
        assert data[name] == str(value)
    assert data["trace_id"] == "a" * 32


@pytest.mark.parametrize("changes", [
    {"event_type": "arbitrary"}, {"outcome": "unknown"}, {"actor_role": "root"},
    {"error_code": "raw error text"}, {"actor_id": "bad"}, {"approval_id": "bad"},
    {"ticket_id": "bad"}, {"operation_id": "bad"}, {"request_id": "bad"},
    {"trace_id": "a" * 31}, {"trace_id": "a" * 33}, {"trace_id": "g" * 32},
])
def test_invalid_fields_are_rejected(changes):
    """拒绝未知事件、结果、角色、错误类别和格式错误的关联编号。"""
    with pytest.raises(ValidationError):
        AuditEventInput.model_validate({"event_type": "approval.requested", "outcome": "succeeded", **changes})


@pytest.mark.parametrize("field", ["password", "access_token", "raw_exception", "details"])
def test_unapproved_sensitive_fields_are_rejected(field):
    """没有自由日志字段，调用方不能额外带入凭据或异常正文。"""
    with pytest.raises(ValidationError):
        AuditEventInput.model_validate({"event_type": "approval.requested", "outcome": "succeeded", field: "test-marker"})


@pytest.mark.parametrize("missing", ["event_type", "outcome"])
def test_event_and_outcome_are_required(missing):
    """每条事件必须说明行为和结果。"""
    data = {"event_type": "approval.requested", "outcome": "succeeded"}
    del data[missing]
    with pytest.raises(ValidationError):
        AuditEventInput.model_validate(data)
