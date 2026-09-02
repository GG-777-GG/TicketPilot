from fastapi.testclient import TestClient

import ticketpilot.api as api
from ticketpilot.api import app
from ticketpilot.models import Ticket


client = TestClient(app)


# 验证健康检查接口返回 200 和固定 JSON
def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "TicketPilot",
    }


# 验证合法请求可以创建并保存工单
def test_create_ticket_returns_created_ticket(
    monkeypatch,
) -> None:
    saved_tickets: list[Ticket] = []

    # 模拟从存储层读取空工单列表
    def fake_load_tickets() -> list[Ticket]:
        return []

    # 记录接口交给存储层保存的工单
    def fake_save_tickets(
        tickets: list[Ticket],
    ) -> None:
        saved_tickets.extend(tickets)

    monkeypatch.setattr(
        api,
        "load_tickets",
        fake_load_tickets,
    )
    monkeypatch.setattr(
        api,
        "save_tickets",
        fake_save_tickets,
    )

    response = client.post(
        "/v1/tickets",
        json={
            "customer_name": "GG",
            "contact_email": "gg@example.com",
            "channel": "web",
            "subject": "无法登录",
            "description": "输入密码后没有反应",
            "priority": "high",
        },
    )

    response_data = response.json()

    assert response.status_code == 201
    assert response_data["id"]
    assert response_data["customer_name"] == "GG"
    assert response_data["status"] == "open"
    assert len(saved_tickets) == 1
    assert saved_tickets[0].id == response_data["id"]


# 验证非法邮箱会被 Pydantic 自动拒绝
def test_create_ticket_rejects_invalid_email() -> None:
    response = client.post(
        "/v1/tickets",
        json={
            "customer_name": "GG",
            "contact_email": "not-an-email",
            "channel": "web",
            "subject": "无法登录",
            "description": "输入密码后没有反应",
            "priority": "high",
        },
    )

    assert response.status_code == 422


# 验证不允许的优先级会被自动拒绝
def test_create_ticket_rejects_invalid_priority() -> None:
    response = client.post(
        "/v1/tickets",
        json={
            "customer_name": "GG",
            "contact_email": "gg@example.com",
            "channel": "web",
            "subject": "无法登录",
            "description": "输入密码后没有反应",
            "priority": "urgent",
        },
    )

    assert response.status_code == 422

# 创建一张供查询接口测试使用的工单
def make_api_ticket(
    ticket_id: str = "T-001",
) -> Ticket:
    return Ticket(
        id=ticket_id,
        customer_name="GG",
        contact_email="gg@example.com",
        channel="web",
        subject="无法登录",
        description="输入密码后没有反应",
        priority="high",
    )


# 验证查询全部工单会返回列表
def test_list_tickets_returns_all_tickets(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [
            make_api_ticket("T-001"),
            make_api_ticket("T-002"),
        ],
    )

    response = client.get("/v1/tickets")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "T-001"
    assert response.json()[1]["id"] == "T-002"


# 验证可以根据路径中的 ID 查询单张工单
def test_get_ticket_returns_matching_ticket(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [
            make_api_ticket("T-001"),
            make_api_ticket("T-002"),
        ],
    )

    response = client.get("/v1/tickets/T-002")

    assert response.status_code == 200
    assert response.json()["id"] == "T-002"


# 验证查询不存在的工单会返回 404
def test_get_ticket_returns_404_when_not_found(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [make_api_ticket("T-001")],
    )

    response = client.get("/v1/tickets/T-999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "工单不存在",
    }


# 验证存储层读取失败会转换为 HTTP 500
def test_list_tickets_returns_500_when_storage_fails(
    monkeypatch,
) -> None:
    def fake_load_tickets() -> list[Ticket]:
        raise RuntimeError("JSON 文件损坏")

    monkeypatch.setattr(
        api,
        "load_tickets",
        fake_load_tickets,
    )

    response = client.get("/v1/tickets")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }

    # 验证可以修改指定工单的状态
def test_update_ticket_status_returns_updated_ticket(
            monkeypatch,
    ) -> None:
        ticket = make_api_ticket("T-001")
        saved_tickets: list[Ticket] = []

        monkeypatch.setattr(
            api,
            "load_tickets",
            lambda: [ticket],
        )

        def fake_save_tickets(
                tickets: list[Ticket],
        ) -> None:
            saved_tickets.extend(tickets)

        monkeypatch.setattr(
            api,
            "save_tickets",
            fake_save_tickets,
        )

        response = client.patch(
            "/v1/tickets/T-001/status",
            json={
                "status": "resolved",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert ticket.status == "resolved"
        assert len(saved_tickets) == 1

# 验证非法工单状态会被 Pydantic 拒绝
def test_update_ticket_status_rejects_invalid_status() -> None:
    response = client.patch(
        "/v1/tickets/T-001/status",
        json={
            "status": "finished",
        },
    )

    assert response.status_code == 422

# 验证修改不存在的工单会返回 404
def test_update_ticket_status_returns_404_when_not_found(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [make_api_ticket("T-001")],
    )

    response = client.patch(
        "/v1/tickets/T-999/status",
        json={
            "status": "resolved",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "工单不存在",
    }

# 验证状态保存失败时会恢复旧状态
def test_update_ticket_status_rolls_back_when_save_fails(
    monkeypatch,
) -> None:
    ticket = make_api_ticket("T-001")

    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [ticket],
    )

    def fake_save_tickets(
        tickets: list[Ticket],
    ) -> None:
        raise RuntimeError("磁盘写入失败")

    monkeypatch.setattr(
        api,
        "save_tickets",
        fake_save_tickets,
    )

    response = client.patch(
        "/v1/tickets/T-001/status",
        json={
            "status": "resolved",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }
    assert ticket.status == "open"

# 验证可以删除指定工单
def test_delete_ticket_returns_204(
    monkeypatch,
) -> None:
    tickets = [
        make_api_ticket("T-001"),
        make_api_ticket("T-002"),
    ]
    saved_tickets: list[Ticket] = []

    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: tickets,
    )

    def fake_save_tickets(
        remaining_tickets: list[Ticket],
    ) -> None:
        saved_tickets.extend(remaining_tickets)

    monkeypatch.setattr(
        api,
        "save_tickets",
        fake_save_tickets,
    )

    response = client.delete(
        "/v1/tickets/T-001",
    )

    assert response.status_code == 204
    assert response.content == b""
    assert len(tickets) == 1
    assert tickets[0].id == "T-002"
    assert len(saved_tickets) == 1
    assert saved_tickets[0].id == "T-002"

# 验证删除不存在的工单会返回 404
def test_delete_ticket_returns_404_when_not_found(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: [make_api_ticket("T-001")],
    )

    response = client.delete(
        "/v1/tickets/T-999",
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "工单不存在",
    }

# 验证删除保存失败时会恢复原工单
def test_delete_ticket_rolls_back_when_save_fails(
    monkeypatch,
) -> None:
    tickets = [
        make_api_ticket("T-001"),
        make_api_ticket("T-002"),
    ]

    monkeypatch.setattr(
        api,
        "load_tickets",
        lambda: tickets,
    )

    def fake_save_tickets(
        remaining_tickets: list[Ticket],
    ) -> None:
        raise RuntimeError("磁盘写入失败")

    monkeypatch.setattr(
        api,
        "save_tickets",
        fake_save_tickets,
    )

    response = client.delete(
        "/v1/tickets/T-001",
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }
    assert len(tickets) == 2
    assert tickets[0].id == "T-001"
    assert tickets[1].id == "T-002"