"""
文件职责：让两个独立Session同时审批，验证仅一个决定成功。
作用与配合：使用文件SQLite、真实Service和条件UPDATE，屏障控制竞争时机。
边界：验证SQLite并发行为，不等同于真实PostgreSQL并发验收。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals import execution, repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalReviewRequest
from ticketpilot.core.database import Base
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


def test_simultaneous_reviews_keep_only_winning_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两个请求都读到pending后才开始更新，失败者不能覆盖胜出者。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'concurrent.sqlite').as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        """独立连接均启用外键检查。"""
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            owner = build_user(role="agent")
            reviewers = [build_user(role="supervisor"), build_user(role="admin")]
            ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com", channel="web", subject="并发审批", description="测试", priority="medium", status="open")
            session.add_all([owner, *reviewers, ticket])
            session.flush()
            record = ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id, requested_by=owner.id, request_reason="申请关闭")
            session.add(record)
            session.commit()
            approval_id, operation_id, ticket_id = record.id, record.operation_id, ticket.id
            reviewer_ids = [user.id for user in reviewers]

        barrier = Barrier(2)
        original_update = repository.review_pending_approval

        def synchronized_update(*args, **kwargs):
            """两个Service都完成查询后，再放行实际UPDATE。"""
            barrier.wait(timeout=5)
            return original_update(*args, **kwargs)

        monkeypatch.setattr(repository, "review_pending_approval", synchronized_update)

        def submit(index: int):
            """每个请求使用独立Session，提交相反决定。"""
            with Session(engine, expire_on_commit=False) as session:
                reviewer = session.get(UserRecord, reviewer_ids[index])
                try:
                    result = service.review_approval(session=session, approval_id=approval_id, request=ApprovalReviewRequest(approved=index == 0, reason=f"决定{index}"), reviewer=reviewer)
                    return "success", result.status, result.reviewed_by, result.approval_reason
                except service.ApprovalConflictError:
                    return "conflict", None, None, None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, index) for index in range(2)]
            results = [future.result(timeout=15) for future in futures]

        assert [result[0] for result in results].count("success") == 1
        assert [result[0] for result in results].count("conflict") == 1
        winner = next(result for result in results if result[0] == "success")
        with Session(engine) as session:
            saved = session.get(ApprovalRecord, approval_id)
            assert (saved.status, saved.reviewed_by, saved.approval_reason) == winner[1:]
            assert saved.operation_id == operation_id and saved.reviewed_at is not None
            assert session.get(TicketRecord, ticket_id).status == "open"
    finally:
        engine.dispose()


@pytest.mark.parametrize("same_request", [True, False])
def test_simultaneous_executions_have_only_one_business_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_request: bool) -> None:
    """同一申请竞争返回同一成功结果；不同申请竞争同工单仅一方成功。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'execution-race.sqlite').as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        """独立连接均实际检查外键。"""
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            owner = build_user(role="agent")
            executors = [build_user(role="supervisor"), build_user(role="admin")]
            ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com", channel="web", subject="并发执行", description="测试", priority="medium", status="open")
            session.add_all([owner, *executors, ticket])
            session.flush()
            records = [ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id, requested_by=owner.id, request_reason="申请关闭", status="approved", reviewed_by=executors[0].id, approval_reason="已核验", reviewed_at=datetime.now(UTC)) for _ in range(1 if same_request else 2)]
            session.add_all(records)
            session.commit()
            approval_ids = [records[0].id, records[0].id if same_request else records[1].id]
            executor_ids = [user.id for user in executors]
            ticket_id = ticket.id

        barrier = Barrier(2)
        original_claim = repository.claim_approved_request

        def synchronized_claim(*args, **kwargs):
            """两个请求均已查到申请，再同时进入条件更新。"""
            barrier.wait(timeout=5)
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(repository, "claim_approved_request", synchronized_claim)

        def submit(index: int):
            """每个执行请求持有自己的数据库Session。"""
            with Session(engine, expire_on_commit=False) as session:
                executor = session.get(UserRecord, executor_ids[index])
                try:
                    result = execution.execute_approval(session=session, approval_id=approval_ids[index], executor=executor)
                    return "success", result.id, result.operation_id, result.result
                except service.ApprovalConflictError:
                    return "conflict", approval_ids[index], None, None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, index) for index in range(2)]
            results = [future.result(timeout=15) for future in futures]

        successes = [result for result in results if result[0] == "success"]
        assert len(successes) == (2 if same_request else 1)
        assert [result[0] for result in results].count("conflict") == (0 if same_request else 1)
        if same_request:
            assert successes[0][1:] == successes[1][1:]

        with Session(engine) as session:
            receipts = list(session.scalars(select(ApprovalExecutionRecord)))
            assert len(receipts) == 1
            assert receipts[0].operation_id == successes[0][2]
            assert receipts[0].result == successes[0][3]
            assert receipts[0].executed_by in executor_ids
            assert session.get(TicketRecord, ticket_id).status == "closed"
            for result in results:
                record = session.get(ApprovalRecord, result[1])
                assert record.status == ("succeeded" if result[0] == "success" else "approved")
    finally:
        engine.dispose()
