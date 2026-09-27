from ticketpilot.legacy_cli.models import Ticket


# 创建一张供自动测试重复使用的工单
def make_ticket() -> Ticket:
    return Ticket(
        id="T-001",
        customer_name="GG",
        contact_email="gg@example.com",
        channel="web",
        subject="无法登录",
        description="输入密码后没有反应",
        priority="high",
    )


# 验证新工单默认状态是 open
def test_ticket_uses_open_as_default_status() -> None:
    ticket = make_ticket()

    assert ticket.status == "open"


# 验证工单对象的状态属性可以修改
def test_ticket_status_can_be_changed() -> None:
    ticket = make_ticket()

    ticket.status = "resolved"

    assert ticket.status == "resolved"
