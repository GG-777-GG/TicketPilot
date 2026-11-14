"""
文件职责：验证正式审批请求的输入边界和响应序列化。
使用方式：通过 pytest 运行，由老师维护并解释关键断言。
作用与配合：直接使用 approvals.schemas，不复制接口模型实现。
边界：不连接数据库，不把输入校验等同于用户认证或权限校验。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from ticketpilot.approvals.schemas import (
    ApprovalCreateRequest,
    ApprovalResponse,
    ApprovalReviewRequest,
)


TICKET_ID = "692afc0a-9260-41fc-a22e-aac3ff55d9cc"
USER_ID = "10000000-0000-4000-8000-000000000001"


def test_create_request_normalizes_reason_and_parses_uuid() -> None:
    """合法输入转换 UUID、清理空白，并采用固定操作类型。"""

    request = ApprovalCreateRequest(
        ticket_id=TICKET_ID,
        request_reason="  客户问题已解决  ",
    )
    assert request.ticket_id == UUID(TICKET_ID)
    assert request.request_reason == "客户问题已解决"
    assert request.action == "close_ticket"


@pytest.mark.parametrize(
    "changes, expected_field",
    [
        ({"ticket_id": "not-a-uuid"}, "ticket_id"),
        ({"request_reason": "   "}, "request_reason"),
        ({"request_reason": "理" * 501}, "request_reason"),
        ({"action": "delete_ticket"}, "action"),
        ({"requested_by": USER_ID}, "requested_by"),
        ({"reviewed_by": USER_ID}, "reviewed_by"),
        ({"status": "succeeded"}, "status"),
    ],
)
def test_create_request_rejects_invalid_or_server_owned_fields(
    changes: dict[str, str],
    expected_field: str,
) -> None:
    """拒绝不合法参数及客户端试图填写的身份、状态字段。"""

    payload = {
        "ticket_id": TICKET_ID,
        "request_reason": "客户问题已解决",
        **changes,
    }
    with pytest.raises(ValidationError) as caught:
        ApprovalCreateRequest.model_validate(payload)

    assert any(error["loc"] == (expected_field,) for error in caught.value.errors())


@pytest.mark.parametrize("approved", [True, False])
def test_review_request_accepts_both_decisions(approved: bool) -> None:
    """批准和拒绝都合法，理由去掉两端空白。"""

    request = ApprovalReviewRequest(approved=approved, reason="  已核验  ")
    assert request.approved is approved
    assert request.reason == "已核验"


@pytest.mark.parametrize("approved", ["true", "false", 0, 1, None])
def test_review_request_rejects_non_boolean_decisions(approved: object) -> None:
    """严格布尔类型不能被字符串、数字或 null 替代。"""

    with pytest.raises(ValidationError) as caught:
        ApprovalReviewRequest.model_validate({"approved": approved, "reason": "已核验"})
    assert caught.value.errors()[0]["loc"] == ("approved",)


@pytest.mark.parametrize(
    "changes, expected_field",
    [
        ({"reason": "   "}, "reason"),
        ({"reason": "理" * 501}, "reason"),
        ({"reviewed_by": USER_ID}, "reviewed_by"),
    ],
)
def test_review_request_rejects_invalid_reason_or_forged_identity(
    changes: dict[str, str],
    expected_field: str,
) -> None:
    """拒绝无效理由及请求体中自报的审批人。"""

    with pytest.raises(ValidationError) as caught:
        ApprovalReviewRequest.model_validate({"approved": True, "reason": "已核验", **changes})
    assert any(error["loc"] == (expected_field,) for error in caught.value.errors())


def test_pending_response_serializes_uuid_and_nullable_review_fields() -> None:
    """待审批记录能明确返回未审批字段，并把 UUID 转成 JSON 字符串。"""

    now = datetime(2026, 11, 10, tzinfo=UTC)
    response = ApprovalResponse(
        id="20000000-0000-4000-8000-000000000001",
        operation_id="30000000-0000-4000-8000-000000000001",
        ticket_id=TICKET_ID,
        action="close_ticket",
        status="pending",
        requested_by=USER_ID,
        reviewed_by=None,
        request_reason="客户问题已解决",
        approval_reason=None,
        result=None,
        created_at=now,
        updated_at=now,
        reviewed_at=None,
    )
    payload = response.model_dump(mode="json")
    assert payload["ticket_id"] == TICKET_ID
    assert payload["requested_by"] == USER_ID
    assert payload["reviewed_by"] is None
    assert payload["reviewed_at"] is None
    assert payload["created_at"].startswith("2026-11-10T00:00:00")

    with pytest.raises(ValidationError):
        ApprovalResponse.model_validate({**payload, "status": "unknown"})
