import ticketpilot.legacy_cli.cli as cli
from ticketpilot.legacy_cli.models import Ticket


# 创建一张供命令行功能测试使用的工单
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


# 验证用户输入可以转换为 Ticket 对象
def test_collect_ticket_creates_ticket(monkeypatch) -> None:
    required_values = iter(
        [
            "GG",
            "gg@example.com",
            "无法登录",
            "输入密码后没有反应",
        ]
    )
    choice_values = iter(["web", "high"])

    # 模拟用户依次输入必填文本
    def fake_ask_required(prompt: str) -> str:
        return next(required_values)

    # 模拟用户依次选择渠道和优先级
    def fake_ask_choice(
        prompt: str,
        allowed_values: set[str],
        error_message: str,
    ) -> str:
        return next(choice_values)

    monkeypatch.setattr(
        cli,
        "ask_required",
        fake_ask_required,
    )
    monkeypatch.setattr(
        cli,
        "ask_choice",
        fake_ask_choice,
    )

    ticket = cli.collect_ticket("T-001")

    assert ticket.id == "T-001"
    assert ticket.customer_name == "GG"
    assert ticket.channel == "web"
    assert ticket.priority == "high"
    assert ticket.status == "open"


# 验证可以按编号找到已有工单
def test_find_ticket_by_id_returns_ticket() -> None:
    ticket = make_ticket()
    tickets = [ticket]

    result = cli.find_ticket_by_id(
        tickets,
        "T-001",
    )

    assert result == ticket


# 验证编号不存在时返回 None
def test_find_ticket_by_id_returns_none() -> None:
    tickets = [make_ticket()]

    result = cli.find_ticket_by_id(
        tickets,
        "T-999",
    )

    assert result is None


# 验证状态修改成功后调用保存
def test_update_ticket_status_saves_changes(
    monkeypatch,
) -> None:
    ticket = make_ticket()
    tickets = [ticket]
    save_calls: list[int] = []

    # 模拟用户输入工单编号
    def fake_ask_required(prompt: str) -> str:
        return "T-001"

    # 模拟用户选择 resolved 状态
    def fake_ask_choice(
        prompt: str,
        allowed_values: set[str],
        error_message: str,
    ) -> str:
        return "resolved"

    # 记录保存函数被调用时的工单数量
    def fake_save_tickets(
        tickets_to_save: list[Ticket],
    ) -> None:
        save_calls.append(len(tickets_to_save))

    monkeypatch.setattr(
        cli,
        "ask_required",
        fake_ask_required,
    )
    monkeypatch.setattr(
        cli,
        "ask_choice",
        fake_ask_choice,
    )
    monkeypatch.setattr(
        cli,
        "save_tickets",
        fake_save_tickets,
    )

    result = cli.update_ticket_status(tickets)

    assert result is True
    assert ticket.status == "resolved"
    assert save_calls == [1]


# 验证保存失败后恢复原来的工单状态
def test_update_ticket_status_rolls_back(
    monkeypatch,
) -> None:
    ticket = make_ticket()
    tickets = [ticket]

    # 模拟用户输入工单编号
    def fake_ask_required(prompt: str) -> str:
        return "T-001"

    # 模拟用户选择 resolved 状态
    def fake_ask_choice(
        prompt: str,
        allowed_values: set[str],
        error_message: str,
    ) -> str:
        return "resolved"

    # 模拟保存时发生磁盘错误
    def fake_save_tickets(
        tickets_to_save: list[Ticket],
    ) -> None:
        raise RuntimeError("模拟磁盘错误")

    monkeypatch.setattr(
        cli,
        "ask_required",
        fake_ask_required,
    )
    monkeypatch.setattr(
        cli,
        "ask_choice",
        fake_ask_choice,
    )
    monkeypatch.setattr(
        cli,
        "save_tickets",
        fake_save_tickets,
    )

    result = cli.update_ticket_status(tickets)

    assert result is False
    assert ticket.status == "open"


# 验证确认删除后工单会从列表移除并保存
def test_delete_ticket_removes_and_saves(
    monkeypatch,
) -> None:
    ticket = make_ticket()
    tickets = [ticket]
    saved_counts: list[int] = []

    # 模拟用户输入工单编号
    def fake_ask_required(prompt: str) -> str:
        return "T-001"

    # 模拟用户确认删除
    def fake_ask_choice(
        prompt: str,
        allowed_values: set[str],
        error_message: str,
    ) -> str:
        return "yes"

    # 记录保存时列表中的工单数量
    def fake_save_tickets(
        tickets_to_save: list[Ticket],
    ) -> None:
        saved_counts.append(len(tickets_to_save))

    monkeypatch.setattr(
        cli,
        "ask_required",
        fake_ask_required,
    )
    monkeypatch.setattr(
        cli,
        "ask_choice",
        fake_ask_choice,
    )
    monkeypatch.setattr(
        cli,
        "save_tickets",
        fake_save_tickets,
    )

    result = cli.delete_ticket(tickets)

    assert result is True
    assert tickets == []
    assert saved_counts == [0]


# 验证删除保存失败后工单会回到原位置
def test_delete_ticket_rolls_back(
    monkeypatch,
) -> None:
    ticket = make_ticket()
    tickets = [ticket]

    # 模拟用户输入工单编号
    def fake_ask_required(prompt: str) -> str:
        return "T-001"

    # 模拟用户确认删除
    def fake_ask_choice(
        prompt: str,
        allowed_values: set[str],
        error_message: str,
    ) -> str:
        return "yes"

    # 模拟保存时发生磁盘错误
    def fake_save_tickets(
        tickets_to_save: list[Ticket],
    ) -> None:
        raise RuntimeError("模拟磁盘错误")

    monkeypatch.setattr(
        cli,
        "ask_required",
        fake_ask_required,
    )
    monkeypatch.setattr(
        cli,
        "ask_choice",
        fake_ask_choice,
    )
    monkeypatch.setattr(
        cli,
        "save_tickets",
        fake_save_tickets,
    )

    result = cli.delete_ticket(tickets)

    assert result is False
    assert tickets == [ticket]
