from sqlalchemy import (
    create_engine,
    select,
)
from sqlalchemy.orm import Session

from ticketpilot.database import Base
from ticketpilot.db_models import TicketRecord


# 验证 ORM 模型可以保存并查询工单
def test_ticket_record_can_be_saved_and_loaded() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
    )
    Base.metadata.create_all(
        bind=engine,
    )

    ticket = TicketRecord(
        id="T-001",
        customer_name="GG",
        contact_email="gg@example.com",
        channel="web",
        subject="无法登录",
        description="输入密码后没有反应",
        priority="high",
        status="open",
    )

    with Session(engine) as session:
        session.add(ticket)
        session.commit()

        statement = select(TicketRecord).where(
            TicketRecord.id == "T-001",
        )
        loaded_ticket = session.scalar(statement)

        assert loaded_ticket is not None
        assert loaded_ticket.id == "T-001"
        assert loaded_ticket.customer_name == "GG"
        assert loaded_ticket.status == "open"