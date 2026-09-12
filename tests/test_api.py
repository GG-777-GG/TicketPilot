from fastapi.testclient import TestClient

import ticketpilot.api as api

# 导入数据库模块，用于构造测试专用数据库
import ticketpilot.database as database
from ticketpilot.api import app

# 导入 ORM 工单，用于模拟数据库 Repository 的返回结果
from ticketpilot.db_models import TicketRecord

# 导入日期时间类型和 UTC 时区
from datetime import (
    UTC,
    datetime,
)

# 测试统一使用固定时间，避免每次运行结果不同
TEST_TIMESTAMP = datetime(
    # 年
    2026,
    # 月
    1,
    # 日
    1,
    # 使用 UTC 时区
    tzinfo=UTC,
)

client = TestClient(app)


# 验证健康检查接口返回 200 和固定 JSON
def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "TicketPilot",
    }


# 验证合法请求会调用 Repository 创建数据库工单
def test_create_ticket_returns_created_ticket(
        # 使用 monkeypatch 隔离真实数据库
        monkeypatch,
) -> None:
    # 创建列表，记录 API 交给 Repository 的 ORM 对象
    saved_ticket_records: list[TicketRecord] = []

    # 定义模拟的 Repository 创建函数
    def fake_create_ticket(
            # 接收路由注入的 Session
            session,
            # 接收 API 构造的 ORM 工单
            ticket_record: TicketRecord,
    ) -> TicketRecord:
        # 模拟数据库为新工单生成创建时间
        ticket_record.created_at = TEST_TIMESTAMP

        # 模拟数据库为新工单生成更新时间
        ticket_record.updated_at = TEST_TIMESTAMP

        # 记录收到的 ORM 工单
        saved_ticket_records.append(
            ticket_record
        )

        # 模拟数据库保存成功并返回该对象
        return ticket_record

    # 临时替换真实 Repository 创建函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定要替换的函数名
        "create_ticket",
        # 指定测试使用的假函数
        fake_create_ticket,
    )

    # 发送创建工单请求
    response = client.post(
        # 请求创建工单接口
        "/v1/tickets",
        # 提交符合 TicketCreateRequest 的 JSON
        json={
            # 客户姓名
            "customer_name": "GG",
            # 联系邮箱
            "contact_email": "gg@example.com",
            # 来源渠道
            "channel": "web",
            # 工单标题
            "subject": "无法登录",
            # 问题描述
            "description": "输入密码后没有反应",
            # 工单优先级
            "priority": "high",
        },
    )

    # 解析 API 返回的 JSON
    response_data = response.json()

    # 验证创建成功状态码
    assert response.status_code == 201

    # 验证 API 生成了非空工单 ID
    assert response_data["id"]

    # 验证客户姓名正确
    assert response_data["customer_name"] == "GG"

    # 验证新工单初始状态为 open
    assert response_data["status"] == "open"

    # 验证新工单创建时还没有分配处理人
    assert response_data["assigned_to"] is None

    # 把 JSON 中的创建时间字符串转换回 datetime
    # 然后检查它是不是我们设置的固定测试时间
    assert (
            datetime.fromisoformat(
                response_data["created_at"]
            )
            == TEST_TIMESTAMP
    )

    # 检查 API 返回的更新时间是否正确
    assert (
            datetime.fromisoformat(
                response_data["updated_at"]
            )
            == TEST_TIMESTAMP
    )

    # 验证 Repository 被调用了一次
    assert len(saved_ticket_records) == 1

    # 验证保存的是 TicketRecord ORM 对象
    assert isinstance(
        saved_ticket_records[0],
        TicketRecord,
    )

    # 验证保存对象与响应使用相同 ID
    assert (
            saved_ticket_records[0].id
            == response_data["id"]
    )


# 验证 Repository 创建失败时 API 返回 HTTP 500
def test_create_ticket_returns_500_when_database_fails(
        # 使用 monkeypatch 模拟 Repository 故障
        monkeypatch,
) -> None:
    # 定义模拟失败的创建函数
    def fake_create_ticket(
            # 接收路由注入的 Session
            session,
            # 接收 API 构造的 ORM 对象
            ticket_record: TicketRecord,
    ) -> TicketRecord:
        # 模拟 Repository 已转换的数据库错误
        raise RuntimeError(
            "创建工单失败"
        )

    # 替换真实 Repository 创建函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定创建函数
        "create_ticket",
        # 指定模拟失败函数
        fake_create_ticket,
    )

    # 发送合法创建请求
    response = client.post(
        # 请求创建工单接口
        "/v1/tickets",
        # 请求体本身合法，失败来自数据库
        json={
            # 客户姓名
            "customer_name": "GG",
            # 合法邮箱
            "contact_email": "gg@example.com",
            # 合法渠道
            "channel": "web",
            # 工单标题
            "subject": "无法登录",
            # 问题描述
            "description": "输入密码后没有反应",
            # 合法优先级
            "priority": "high",
        },
    )

    # 验证数据库错误被转换为 HTTP 500
    assert response.status_code == 500

    # 验证没有向客户端泄漏内部异常
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }


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


# 创建一张供数据库查询接口测试使用的 ORM 工单
def make_database_ticket(
        # 允许测试指定不同的工单 ID
        ticket_id: str = "T-001",
        # 返回尚未保存的 TicketRecord 对象
) -> TicketRecord:
    # 创建完整 ORM 工单
    return TicketRecord(
        # 设置主键
        id=ticket_id,
        # 设置客户姓名
        customer_name="GG",
        # 设置联系邮箱
        contact_email="gg@example.com",
        # 设置来源渠道
        channel="web",
        # 设置工单标题
        subject="无法登录",
        # 设置问题描述
        description="输入密码后没有反应",
        # 设置优先级
        priority="high",
        # 设置工单状态
        status="open",
        # 模拟数据库生成的创建时间
        created_at=TEST_TIMESTAMP,

        # 模拟数据库生成的更新时间
        updated_at=TEST_TIMESTAMP,
    )


# 验证列表接口会返回 Repository 查询到的数据库工单
def test_list_tickets_returns_all_tickets(
        # 使用 monkeypatch 隔离真实数据库
        monkeypatch,
) -> None:
    # 临时替换 Repository 的列表查询函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定需要替换的函数名
        "list_tickets",
        # 模拟 Repository 返回两张 ORM 工单
        lambda session: [
            make_database_ticket("T-001"),
            make_database_ticket("T-002"),
        ],
    )

    # 向 FastAPI 发送查询全部工单请求
    response = client.get(
        "/v1/tickets"
    )

    # 验证请求成功
    assert response.status_code == 200

    # 验证返回两张工单
    assert len(response.json()) == 2

    # 验证第一张工单 ID
    assert response.json()[0]["id"] == "T-001"

    # 验证第二张工单 ID
    assert response.json()[1]["id"] == "T-002"


# 验证单张查询接口会返回 Repository 找到的数据库工单
def test_get_ticket_returns_matching_ticket(
        # 使用 monkeypatch 替换 Repository
        monkeypatch,
) -> None:
    # 定义模拟的主键查询函数
    def fake_get_ticket_by_id(
            # 接收路由传入的 Session
            session,
            # 接收路由传入的目标 ID
            ticket_id: str,
    ) -> TicketRecord:
        # 返回与目标 ID 对应的 ORM 工单
        return make_database_ticket(
            ticket_id
        )

    # 替换真正的 Repository 主键查询
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定被替换的函数
        "get_ticket_by_id",
        # 指定测试替代函数
        fake_get_ticket_by_id,
    )

    # 请求 T-002
    response = client.get(
        "/v1/tickets/T-002"
    )

    # 验证请求成功
    assert response.status_code == 200

    # 验证返回的是请求的工单
    assert response.json()["id"] == "T-002"


# 验证 Repository 找不到工单时接口返回 404
def test_get_ticket_returns_404_when_not_found(
        # 使用 monkeypatch 隔离真实数据库
        monkeypatch,
) -> None:
    # 模拟 Repository 查询不到工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定主键查询函数
        "get_ticket_by_id",
        # 无论查询什么 ID 都返回 None
        lambda session, ticket_id: None,
    )

    # 请求一张不存在的工单
    response = client.get(
        "/v1/tickets/T-999"
    )

    # 验证返回 HTTP 404
    assert response.status_code == 404

    # 验证错误响应内容
    assert response.json() == {
        "detail": "工单不存在",
    }


# 验证 Repository 查询失败时接口返回 HTTP 500
def test_list_tickets_returns_500_when_database_fails(
        # 使用 monkeypatch 模拟数据库故障
        monkeypatch,
) -> None:
    # 定义模拟失败的 Repository 函数
    def fake_list_tickets(
            # 接收路由注入的 Session
            session,
    ) -> list[TicketRecord]:
        # 模拟 Repository 已转换的数据库错误
        raise RuntimeError(
            "查询工单列表失败"
        )

    # 替换真正的 Repository 列表查询
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定列表查询函数
        "list_tickets",
        # 指定模拟失败函数
        fake_list_tickets,
    )

    # 请求全部工单
    response = client.get(
        "/v1/tickets"
    )

    # 验证转换成 HTTP 500
    assert response.status_code == 500

    # 验证没有泄漏底层数据库信息
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }

    # 验证可以修改指定工单的状态


# 验证 PATCH 接口通过 Repository 修改数据库工单
def test_update_ticket_status_returns_updated_ticket(
        # 使用 monkeypatch 替换数据库操作
        monkeypatch,
) -> None:
    # 创建模拟 Repository 查询结果
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 记录 Repository 收到的新状态
    received_statuses: list[str] = []

    # 模拟根据 ID 找到数据库工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定查询函数
        "get_ticket_by_id",
        # 返回准备好的 ORM 工单
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟的状态更新函数
    def fake_update_ticket_status(
            # 接收当前请求的 Session
            session,
            # 接收需要修改的 ORM 工单
            stored_ticket: TicketRecord,
            # 接收新的状态
            new_status: str,
    ) -> TicketRecord:
        # 记录收到的状态
        received_statuses.append(
            new_status
        )

        # 模拟 Repository 修改 ORM 对象
        stored_ticket.status = new_status

        # 模拟保存成功并返回对象
        return stored_ticket

    # 替换真实的 Repository 更新函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定更新函数
        "update_ticket_status",
        # 指定测试替代函数
        fake_update_ticket_status,
    )

    # 发送 PATCH 请求
    response = client.patch(
        # 修改 T-001
        "/v1/tickets/T-001/status",
        # 提交合法的新状态
        json={
            "status": "resolved",
        },
    )

    # 验证 HTTP 请求成功
    assert response.status_code == 200

    # 验证响应状态已经修改
    assert response.json()["status"] == "resolved"

    # 验证 Repository 收到了新状态
    assert received_statuses == [
        "resolved"
    ]


# 验证非法工单状态会被 Pydantic 拒绝
def test_update_ticket_status_rejects_invalid_status() -> None:
    response = client.patch(
        "/v1/tickets/T-001/status",
        json={
            "status": "finished",
        },
    )

    assert response.status_code == 422


# 验证修改不存在的数据库工单时返回 404
def test_update_ticket_status_returns_404_when_not_found(
        # 使用 monkeypatch 模拟查询结果
        monkeypatch,
) -> None:
    # 模拟 Repository 找不到工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定主键查询函数
        "get_ticket_by_id",
        # 返回 None 表示不存在
        lambda session, ticket_id: None,
    )

    # 尝试修改不存在的工单
    response = client.patch(
        # 请求不存在的 ID
        "/v1/tickets/T-999/status",
        # 提交合法状态
        json={
            "status": "resolved",
        },
    )

    # 验证返回 HTTP 404
    assert response.status_code == 404

    # 验证错误内容
    assert response.json() == {
        "detail": "工单不存在",
    }


# 验证 Repository 修改失败时 PATCH 接口返回 500
def test_update_ticket_status_returns_500_when_database_fails(
        # 使用 monkeypatch 模拟数据库故障
        monkeypatch,
) -> None:
    # 创建已经存在的 ORM 工单
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 模拟根据 ID 找到工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定查询函数
        "get_ticket_by_id",
        # 返回目标 ORM 工单
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟失败的更新函数
    def fake_update_ticket_status(
            # 接收当前 Session
            session,
            # 接收目标 ORM 工单
            stored_ticket: TicketRecord,
            # 接收新状态
            new_status: str,
    ) -> TicketRecord:
        # 模拟 Repository 更新失败
        raise RuntimeError(
            "修改工单状态失败"
        )

    # 替换真实 Repository 更新函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定更新函数
        "update_ticket_status",
        # 指定失败函数
        fake_update_ticket_status,
    )

    # 发送合法 PATCH 请求
    response = client.patch(
        # 请求目标工单
        "/v1/tickets/T-001/status",
        # 请求体合法，错误来自数据库
        json={
            "status": "resolved",
        },
    )

    # 验证 API 返回 HTTP 500
    assert response.status_code == 500

    # 验证没有泄漏内部错误
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }

# 验证处理人接口可以分配和取消分配工单
def test_update_ticket_assignee_assigns_and_clears(
        # 使用 monkeypatch 隔离真实数据库
        monkeypatch,
) -> None:
    # 创建一张模拟数据库工单
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 保存 Repository 每次收到的处理人
    received_assignees: list[str | None] = []

    # 模拟根据 ID 找到工单
    monkeypatch.setattr(
        api.ticket_repository,
        "get_ticket_by_id",
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟的 Repository 处理人修改函数
    def fake_update_ticket_assignee(
            # 接收当前请求的 Session
            session,
            # 接收需要修改的 ORM 工单
            stored_ticket: TicketRecord,
            # 接收新处理人或 None
            new_assignee: str | None,
    ) -> TicketRecord:
        # 验证 API 传入的是查询得到的工单
        assert stored_ticket is ticket_record

        # 记录 API 传入的处理人
        received_assignees.append(
            new_assignee
        )

        # 模拟 Repository 修改 ORM 对象
        stored_ticket.assigned_to = new_assignee

        # 模拟保存成功并返回工单
        return stored_ticket

    # 替换真实 Repository 修改方法
    monkeypatch.setattr(
        api.ticket_repository,
        "update_ticket_assignee",
        fake_update_ticket_assignee,
    )

    # 第一次请求：把工单分配给张三
    assigned_response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={
            # 两端空格应该由 Pydantic 自动删除
            "assigned_to": "  张三  ",
        },
    )

    # 第二次请求：使用 null 取消分配
    cleared_response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={
            "assigned_to": None,
        },
    )

    # 验证两次请求都成功
    assert assigned_response.status_code == 200
    assert cleared_response.status_code == 200

    # 验证第一次响应返回清理空格后的姓名
    assert (
        assigned_response.json()["assigned_to"]
        == "张三"
    )

    # 验证第二次响应返回 null
    assert (
        cleared_response.json()["assigned_to"]
        is None
    )

    # 验证 Repository 依次收到分配和取消分配操作
    assert received_assignees == [
        "张三",
        None,
    ]

# 验证不合法的处理人请求会返回 HTTP 422
def test_update_ticket_assignee_rejects_invalid_request() -> None:
    # 请求体缺少 assigned_to 字段
    missing_response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={},
    )

    # 空格被清除后长度为零
    blank_response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={
            "assigned_to": "   ",
        },
    )

    # 处理人姓名超过数据库允许的 100 个字符
    too_long_response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={
            "assigned_to": "A" * 101,
        },
    )

    # 三种不合法请求都应该被 Pydantic 拒绝
    assert missing_response.status_code == 422
    assert blank_response.status_code == 422
    assert too_long_response.status_code == 422

# 验证修改不存在工单的处理人时返回 HTTP 404
def test_update_ticket_assignee_returns_404_when_not_found(
        # 使用 monkeypatch 模拟查询不到工单
        monkeypatch,
) -> None:
    # Repository 返回 None，表示工单不存在
    monkeypatch.setattr(
        api.ticket_repository,
        "get_ticket_by_id",
        lambda session, ticket_id: None,
    )

    # 请求修改不存在的工单
    response = client.patch(
        "/v1/tickets/T-999/assignee",
        json={
            "assigned_to": "张三",
        },
    )

    # 验证资源不存在状态码
    assert response.status_code == 404

    # 验证错误响应内容
    assert response.json() == {
        "detail": "工单不存在",
    }

# 验证 Repository 修改失败时接口返回 HTTP 500
def test_update_ticket_assignee_returns_500_when_database_fails(
        # 使用 monkeypatch 模拟数据库故障
        monkeypatch,
) -> None:
    # 创建一张模拟存在的 ORM 工单
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 模拟根据 ID 找到工单
    monkeypatch.setattr(
        api.ticket_repository,
        "get_ticket_by_id",
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟失败的 Repository 方法
    def fake_update_ticket_assignee(
            # 接收当前请求的 Session
            session,
            # 接收目标工单
            stored_ticket: TicketRecord,
            # 接收准备保存的处理人
            new_assignee: str | None,
    ) -> TicketRecord:
        # 模拟 Repository 已经转换过的数据库异常
        raise RuntimeError(
            "修改工单处理人失败"
        )

    # 替换真实 Repository 方法
    monkeypatch.setattr(
        api.ticket_repository,
        "update_ticket_assignee",
        fake_update_ticket_assignee,
    )

    # 发送格式合法的请求
    response = client.patch(
        "/v1/tickets/T-001/assignee",
        json={
            "assigned_to": "张三",
        },
    )

    # 验证数据库错误被转换成 HTTP 500
    assert response.status_code == 500

    # 验证客户端看不到底层数据库错误
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }
# 验证 DELETE 接口会删除 Repository 找到的 ORM 工单
def test_delete_ticket_returns_204(
        # 使用 monkeypatch 隔离真实数据库
        monkeypatch,
) -> None:
    # 创建模拟查询到的 ORM 工单
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 记录 Repository 收到的删除对象
    deleted_tickets: list[TicketRecord] = []

    # 模拟根据 ID 找到工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定查询函数
        "get_ticket_by_id",
        # 返回准备好的 ORM 工单
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟删除函数
    def fake_delete_ticket(
            # 接收当前 Session
            session,
            # 接收要删除的 ORM 对象
            stored_ticket: TicketRecord,
    ) -> None:
        # 记录被删除对象
        deleted_tickets.append(
            stored_ticket
        )

    # 替换真实 Repository 删除函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定删除函数
        "delete_ticket",
        # 指定测试替代函数
        fake_delete_ticket,
    )

    # 发送删除请求
    response = client.delete(
        "/v1/tickets/T-001"
    )

    # 验证删除成功状态码
    assert response.status_code == 204

    # 验证 HTTP 204 没有响应正文
    assert response.content == b""

    # 验证 Repository 收到一个删除对象
    assert len(deleted_tickets) == 1

    # 验证删除的是目标工单
    assert deleted_tickets[0].id == "T-001"


# 验证删除不存在的数据库工单会返回 404
def test_delete_ticket_returns_404_when_not_found(
        # 使用 monkeypatch 模拟查询结果
        monkeypatch,
) -> None:
    # 模拟 Repository 查询不到工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定查询函数
        "get_ticket_by_id",
        # 返回 None
        lambda session, ticket_id: None,
    )

    # 请求删除不存在的工单
    response = client.delete(
        "/v1/tickets/T-999"
    )

    # 验证 HTTP 404
    assert response.status_code == 404

    # 验证错误响应
    assert response.json() == {
        "detail": "工单不存在",
    }


# 验证 Repository 删除失败时 API 返回 HTTP 500
def test_delete_ticket_returns_500_when_database_fails(
        # 使用 monkeypatch 模拟数据库错误
        monkeypatch,
) -> None:
    # 创建模拟数据库工单
    ticket_record = make_database_ticket(
        "T-001"
    )

    # 模拟找到目标工单
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定查询函数
        "get_ticket_by_id",
        # 返回准备好的 ORM 工单
        lambda session, ticket_id: ticket_record,
    )

    # 定义模拟失败的删除函数
    def fake_delete_ticket(
            # 接收当前 Session
            session,
            # 接收准备删除的 ORM 工单
            stored_ticket: TicketRecord,
    ) -> None:
        # 模拟 Repository 删除失败
        raise RuntimeError(
            "删除工单失败"
        )

    # 替换真实 Repository 删除函数
    monkeypatch.setattr(
        # 指定 Repository 模块
        api.ticket_repository,
        # 指定删除函数
        "delete_ticket",
        # 指定失败函数
        fake_delete_ticket,
    )

    # 发送删除请求
    response = client.delete(
        "/v1/tickets/T-001"
    )

    # 验证转换成 HTTP 500
    assert response.status_code == 500

    # 验证客户端只收到统一错误
    assert response.json() == {
        "detail": "工单服务暂时不可用",
    }


# 验证完整 CRUD 接口可以通过依赖注入访问真实测试数据库
def test_api_uses_database_repository(
        # pytest 提供临时目录，避免污染项目真实数据库
        tmp_path,

) -> None:
    # 在 pytest 临时目录中定义测试数据库文件
    database_file = tmp_path / "api-test.db"

    # 把临时数据库路径转换成 SQLAlchemy URL
    database_url = (
        f"sqlite:///{database_file.as_posix()}"
    )

    # 创建只连接测试数据库的 Engine
    test_engine = database.build_engine(
        database_url
    )

    # 创建测试数据库中的 tickets 表
    database.create_tables(
        test_engine
    )

    # 创建绑定测试 Engine 的 Session 工厂
    test_session_factory = (
        database.build_session_factory(
            test_engine
        )
    )

    # 创建一个 Session，准备测试数据
    with test_session_factory() as session:
        # 创建第一张 ORM 工单
        first_ticket = make_database_ticket(
            "T-001"
        )

        # 创建第二张 ORM 工单
        second_ticket = make_database_ticket(
            "T-002"
        )

        # 通过真实 Repository 保存第一张工单
        api.ticket_repository.create_ticket(
            session,
            first_ticket,
        )

        # 通过真实 Repository 保存第二张工单
        api.ticket_repository.create_ticket(
            session,
            second_ticket,
        )

    # 定义测试专用的 Session 依赖
    def override_get_session():
        # 每次 HTTP 请求都创建一个测试数据库 Session
        with test_session_factory() as session:
            # 把测试 Session 注入 FastAPI 路由
            yield session

    # 告诉 FastAPI 用测试依赖替代正式 get_session
    app.dependency_overrides[
        # 原来的依赖函数
        api.get_session
        # 替换成测试专用依赖函数
    ] = override_get_session

    # 使用 try 保证测试结束后一定清理依赖覆盖
    try:
        # 使用测试客户端调用真实测试数据库中的 CRUD 接口
        with TestClient(app) as database_client:
            # 通过 POST 接口向真实测试数据库创建工单
            create_response = database_client.post(
                # 调用创建工单接口
                "/v1/tickets",
                # 提交合法请求数据
                json={
                    # 客户姓名
                    "customer_name": "Database User",
                    # 联系邮箱
                    "contact_email": "db@example.com",
                    # 来源渠道
                    "channel": "email",
                    # 工单标题
                    "subject": "数据库创建测试",
                    # 问题描述
                    "description": "验证 POST 写入数据库",
                    # 工单优先级
                    "priority": "medium",
                },
            )

            # 从创建响应中取得 API 自动生成的工单 ID
            created_ticket_id = (
                create_response.json()["id"]
            )
            # 通过真实数据库查询全部工单
            list_response = database_client.get(
                "/v1/tickets"
            )

            # 根据刚创建的 ID 从真实数据库重新查询
            detail_response = database_client.get(
                f"/v1/tickets/{created_ticket_id}"
            )

            # 通过真实 PATCH 接口修改刚创建的工单
            update_response = database_client.patch(
                f"/v1/tickets/{created_ticket_id}/status",
                json={
                    "status": "resolved",
                },
            )

            # 再次查询，验证新状态确实写入数据库
            updated_detail_response = database_client.get(
                f"/v1/tickets/{created_ticket_id}"
            )
            # 通过真实处理人接口把工单分配给张三
            assign_response = database_client.patch(
                # 请求刚刚创建的工单
                f"/v1/tickets/{created_ticket_id}/assignee",
                # 提交新的处理人
                json={
                    "assigned_to": "张三",
                },
            )

            # 使用新的 HTTP 请求重新查询，验证处理人已经持久化
            assigned_detail_response = database_client.get(
                f"/v1/tickets/{created_ticket_id}"
            )

            # 通过真实处理人接口取消分配
            clear_assignee_response = database_client.patch(
                # 请求同一张工单
                f"/v1/tickets/{created_ticket_id}/assignee",
                # JSON null 会被 Pydantic 转换成 Python None
                json={
                    "assigned_to": None,
                },
            )

            # 再次查询，验证数据库已经保存 NULL
            cleared_detail_response = database_client.get(
                f"/v1/tickets/{created_ticket_id}"
            )

            # 通过真实 DELETE 接口删除刚创建的工单
            delete_response = database_client.delete(
                f"/v1/tickets/{created_ticket_id}"
            )

            # 删除后再次查询同一张工单
            missing_response = database_client.get(
                f"/v1/tickets/{created_ticket_id}"
            )
        # 验证 POST 创建成功
        assert create_response.status_code == 201

        # 验证新工单初始状态
        assert create_response.json()["status"] == "open"
        # 验证新工单默认没有处理人
        assert create_response.json()["assigned_to"] is None
        # 验证列表查询成功
        assert list_response.status_code == 200

        # 验证两张预置工单加一张新工单，共三张
        assert len(list_response.json()) == 3

        # 验证详情查询成功
        assert detail_response.status_code == 200

        # 验证 GET 查询到的正是 POST 创建的工单
        assert (
                detail_response.json()["id"]
                == created_ticket_id
        )

        # 验证 PATCH 修改成功
        assert update_response.status_code == 200

        # 验证 PATCH 响应包含新状态
        assert update_response.json()["status"] == "resolved"

        # 验证重新查询时数据库仍然保存新状态
        assert (
                updated_detail_response.json()["status"]
                == "resolved"
        )

        # 验证分配处理人的请求成功
        assert assign_response.status_code == 200

        # 验证分配接口立即返回新的处理人
        assert (
                assign_response.json()["assigned_to"]
                == "张三"
        )

        # 验证新的 GET 请求仍能从数据库读到张三
        assert (
                assigned_detail_response.json()["assigned_to"]
                == "张三"
        )

        # 验证取消分配请求成功
        assert clear_assignee_response.status_code == 200

        # 验证取消分配接口返回 JSON null
        assert (
                clear_assignee_response.json()["assigned_to"]
                is None
        )

        # 验证新的 GET 请求从数据库读到 NULL
        assert (
                cleared_detail_response.json()["assigned_to"]
                is None
        )

        # 验证 DELETE 删除成功且不返回正文
        assert delete_response.status_code == 204
        assert delete_response.content == b""

        # 验证删除后查询同一工单返回 404
        assert missing_response.status_code == 404

    # 无论测试是否通过，都执行清理
    finally:
        # 删除 FastAPI 测试依赖覆盖，避免影响后续测试
        app.dependency_overrides.clear()

        # 释放测试 Engine 管理的数据库连接
        test_engine.dispose()
