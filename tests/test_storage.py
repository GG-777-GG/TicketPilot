import pytest

import ticketpilot.storage as storage
from ticketpilot.models import Ticket


# 创建一张供存储测试使用的工单
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


# 验证数据文件不存在时返回空列表
def test_load_tickets_returns_empty_list(
    tmp_path,
    monkeypatch,
) -> None:
    temporary_file = tmp_path / "tickets.json"

    monkeypatch.setattr(
        storage,
        "DATA_FILE",
        temporary_file,
    )

    tickets = storage.load_tickets()

    assert tickets == []


# 验证 Ticket 对象可以保存并重新加载
def test_save_and_load_tickets(
    tmp_path,
    monkeypatch,
) -> None:
    temporary_file = tmp_path / "tickets.json"

    monkeypatch.setattr(
        storage,
        "DATA_FILE",
        temporary_file,
    )

    original_tickets = [make_ticket()]

    storage.save_tickets(original_tickets)
    loaded_tickets = storage.load_tickets()

    assert temporary_file.exists()
    assert loaded_tickets == original_tickets
    assert loaded_tickets[0].status == "open"


# 验证损坏的 JSON 会转换成 RuntimeError
def test_load_tickets_rejects_invalid_json(
    tmp_path,
    monkeypatch,
) -> None:
    temporary_file = tmp_path / "tickets.json"
    temporary_file.write_text(
        "{invalid json",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        storage,
        "DATA_FILE",
        temporary_file,
    )

    with pytest.raises(
        RuntimeError,
        match="读取工单数据失败",
    ):
        storage.load_tickets()