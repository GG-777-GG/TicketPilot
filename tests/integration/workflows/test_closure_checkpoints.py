"""
文件职责：验证正式工单工作流检查点的文件持久化和跨进程恢复。
作用与配合：组合closure_checkpoints、正式图和审批Service，使用临时文件库。
边界：子进程仅访问测试路径；验证正常退出及节点异常后的恢复，
不验证操作系统强制终止、断电、HTTP身份认证或真实PostgreSQL并发。
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.types import Command
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from tests.factories.identity import build_user
from ticketpilot.workflows.ticket_closure.checkpoints import open_ticket_closure_workflow
from ticketpilot.workflows.ticket_closure.state import TicketClosureContext
from ticketpilot.approvals import service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest, ApprovalReviewRequest
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.core.database import Base
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 每次由新的解释器执行，不沿用父进程的图、连接或用户对象。
# 子进程只接收临时数据库路径和测试编号，不读取或输出业务凭据。
PROCESS_CODE = '''
import json, os, sys
from pathlib import Path
from langgraph.types import Command
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from ticketpilot.workflows.ticket_closure import graph as closure
from ticketpilot.workflows.ticket_closure.checkpoints import open_ticket_closure_workflow
from ticketpilot.approvals import service
from ticketpilot.identity.models import UserRecord

args = json.loads(sys.argv[1])
engine = create_engine("sqlite:///" + args["business_path"])
@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, record):
    connection.execute("PRAGMA foreign_keys=ON")

if args["action"] == "commit_then_error":
    original_node = closure.execute_ticket_closure
    def fail_after_commit(state, runtime):
        original_node(state, runtime)
        raise RuntimeError("expected error after business commit")
    closure.execute_ticket_closure = fail_after_commit

error_type = None
try:
    with open_ticket_closure_workflow(Path(args["checkpoint_path"])) as graph:
        config = {"configurable": {"thread_id": args["thread_id"]}}
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            user = session.get(UserRecord, args["user_id"])
            assert user is not None
            if args["action"] in ("start", "commit_then_error"):
                graph_input = {"approval_id": args["approval_id"], "operation_id": "",
                               "ticket_id": "", "approval_status": None, "result": ""}
            elif args["action"] == "resume":
                graph_input = Command(resume={"recheck": True})
            else:
                graph_input = None
            try:
                if args["action"] != "status":
                    graph.invoke(graph_input, config=config,
                                 context=closure.TicketClosureContext(session=session, viewer=user))
            except (service.ApprovalAccessError, service.ApprovalNotFoundError) as error:
                error_type = type(error).__name__
            except RuntimeError as error:
                if args["action"] != "commit_then_error" or str(error) != "expected error after business commit":
                    raise
                error_type = type(error).__name__
        snapshot = graph.get_state(config)
        report = {"values": snapshot.values, "next": list(snapshot.next),
                  "waiting": any(task.interrupts for task in snapshot.tasks),
                  "failed": any(task.error for task in snapshot.tasks),
                  "error_type": error_type, "pid": os.getpid()}
finally:
    engine.dispose()
print(json.dumps(report))
'''


@pytest.fixture
def persisted_case(tmp_path: Path):
    """创建可供多个进程访问的临时业务库，检查点使用另一独立文件。"""
    business_path = tmp_path / "business.sqlite"
    engine = create_engine("sqlite:///" + business_path.as_posix())

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        """所有测试连接均启用外键检查。"""
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[
        UserRecord.__table__, TicketRecord.__table__,
        ApprovalRecord.__table__, ApprovalExecutionRecord.__table__,
        # 正式Service现在会在业务事务中写审计，跨进程测试库也必须建表。
        AuditEventRecord.__table__,
    ])
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        owner, manager = build_user(role="agent"), build_user(role="supervisor")
        ticket = TicketRecord(
            id=str(uuid4()), customer_name="测试客户", contact_email="customer@example.com",
            channel="web", subject="持久恢复测试", description="问题已解决", priority="medium", status="open",
        )
        session.add_all([owner, manager, ticket])
        session.commit()
        record = service.create_approval(
            session=session,
            request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"),
            requester=owner,
        )
        case = SimpleNamespace(
            business_path=business_path, checkpoint_path=tmp_path / "nested" / "checkpoints.sqlite",
            factory=factory, owner_id=owner.id, manager_id=manager.id,
            ticket_id=ticket.id, approval_id=record.id, operation_id=record.operation_id,
            thread_id=str(uuid4()),
        )
    try:
        yield case
    finally:
        engine.dispose()


def initial_state(case):
    """启动同一申请；实际目标和操作编号由节点查询数据库。"""
    return {"approval_id": case.approval_id, "operation_id": "", "ticket_id": "",
            "approval_status": None, "result": ""}


def config_for(case):
    """每次打开文件仍沿用原会话编号。"""
    return {"configurable": {"thread_id": case.thread_id}}


def invoke_local(case, graph, graph_input):
    """每次本机调用也使用新的业务Session与当前用户。"""
    with case.factory() as session:
        return graph.invoke(
            graph_input, config=config_for(case),
            context=TicketClosureContext(session=session, viewer=session.get(UserRecord, case.owner_id)),
        )


def review(case, approved: bool = True):
    """独立事务调用真实审核服务，模拟等待期间发生的审批。"""
    with case.factory() as session:
        service.review_approval(
            session=session, approval_id=case.approval_id,
            request=ApprovalReviewRequest(approved=approved, reason="持久恢复验收"),
            reviewer=session.get(UserRecord, case.manager_id),
        )


def business_snapshot(case):
    """独立连接核对全部业务列，检查恢复是否重复写入。"""
    with case.factory() as session:
        return {
            model.__tablename__: [dict(row) for row in session.execute(select(model.__table__)).mappings()]
            for model in (TicketRecord, ApprovalRecord, ApprovalExecutionRecord)
        }


def run_process(case, action: str, *, user_id=None):
    """启动有超时限制的新解释器；业务路径全部来自tmp_path。"""
    assert case.business_path.parent == case.checkpoint_path.parent.parent
    assert case.business_path.name == "business.sqlite"
    assert case.checkpoint_path.name == "checkpoints.sqlite"
    payload = {
        "business_path": case.business_path.as_posix(), "checkpoint_path": str(case.checkpoint_path),
        "approval_id": case.approval_id, "thread_id": case.thread_id,
        "user_id": user_id or case.owner_id, "action": action,
    }
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", PROCESS_CODE, json.dumps(payload)],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("raise_inside", [False, True])
def test_checkpoint_connection_closes_on_exit(tmp_path: Path, raise_inside: bool):
    """正常或异常退出with都关闭文件连接，调用方不能继续使用旧图。"""
    path = tmp_path / "nested" / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "resource-test"}}
    if raise_inside:
        with pytest.raises(RuntimeError, match="测试调用方异常"):
            with open_ticket_closure_workflow(path) as graph:
                assert graph.get_state(config).values == {}
                connection = graph.checkpointer.conn
                raise RuntimeError("测试调用方异常")
    else:
        with open_ticket_closure_workflow(path) as graph:
            assert graph.get_state(config).values == {}
            connection = graph.checkpointer.conn
    assert path.is_file()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with open_ticket_closure_workflow(path) as reopened:
        assert reopened.get_state(config).values == {}


@pytest.mark.parametrize("changed", ["thread_id", "file"])
def test_other_file_or_thread_does_not_replace_original(persisted_case, changed: str):
    """更换文件或会话编号得到空状态；原文件中的原会话依然保留。"""
    case = persisted_case
    before = business_snapshot(case)
    with open_ticket_closure_workflow(case.checkpoint_path) as graph:
        assert invoke_local(case, graph, initial_state(case))["__interrupt__"]
    path = case.checkpoint_path if changed == "thread_id" else case.checkpoint_path.with_name("other.sqlite")
    config = {"configurable": {"thread_id": str(uuid4())}} if changed == "thread_id" else config_for(case)
    with open_ticket_closure_workflow(path) as graph:
        assert graph.get_state(config).values == {}
    with open_ticket_closure_workflow(case.checkpoint_path) as graph:
        saved = graph.get_state(config_for(case))
        assert saved.values["approval_id"] == case.approval_id
        assert saved.values["operation_id"] == case.operation_id
        assert saved.next == ("wait_for_approval",)
    assert business_snapshot(case) == before


def test_reopen_and_forged_resume_still_waits(persisted_case):
    """关闭连接后重开原文件，伪造批准仍复查并保持暂停。"""
    case = persisted_case
    before = business_snapshot(case)
    with open_ticket_closure_workflow(case.checkpoint_path) as graph:
        assert invoke_local(case, graph, initial_state(case))["__interrupt__"]
    with open_ticket_closure_workflow(case.checkpoint_path) as graph:
        result = invoke_local(case, graph, Command(resume={"approved": True}))
        assert result["__interrupt__"] and result["approval_status"] == "pending"
        assert result["operation_id"] == case.operation_id
    assert business_snapshot(case) == before


@pytest.mark.parametrize("approved", [True, False])
def test_new_process_resumes_after_review(persisted_case, approved: bool):
    """进程A保存等待状态后正常退出，进程B复查并执行批准或结束拒绝。"""
    case = persisted_case
    first = run_process(case, "start")
    assert first["waiting"] and first["values"]["approval_status"] == "pending"
    review(case, approved)
    before_resume = business_snapshot(case)
    resumed = run_process(case, "resume", user_id=case.manager_id)
    assert first["pid"] != resumed["pid"]
    assert resumed["error_type"] is None and resumed["next"] == []
    assert resumed["values"]["operation_id"] == case.operation_id
    stored = business_snapshot(case)
    if approved:
        assert resumed["values"]["approval_status"] == "succeeded"
        assert stored["tickets"][0]["status"] == "closed"
        assert stored["approval_requests"][0]["status"] == "succeeded"
        assert len(stored["approval_executions"]) == 1
        receipt = stored["approval_executions"][0]
        assert receipt["operation_id"] == case.operation_id and receipt["executed_by"] == case.manager_id
        assert receipt["result"] == resumed["values"]["result"]
    else:
        assert resumed["values"]["approval_status"] == "rejected"
        assert stored == before_resume
    # 第三个进程只读检查点，确认完成结果也已写入文件。
    final = run_process(case, "status")
    assert final["values"] == resumed["values"] and final["next"] == []
    assert business_snapshot(case) == stored


def test_new_process_recovers_node_error_after_business_commit(persisted_case):
    """节点提交后抛异常并退出进程，新进程继续原执行节点且不重复生效。"""
    case = persisted_case
    review(case)
    failed = run_process(case, "commit_then_error", user_id=case.manager_id)
    assert failed["error_type"] == "RuntimeError" and failed["failed"]
    assert failed["next"] == ["execute_ticket_closure"]
    assert failed["values"]["approval_status"] == "approved"
    committed = business_snapshot(case)
    assert committed["tickets"][0]["status"] == "closed"
    assert committed["approval_requests"][0]["status"] == "succeeded"
    assert len(committed["approval_executions"]) == 1
    retried = run_process(case, "retry", user_id=case.manager_id)
    assert retried["error_type"] is None and not retried["failed"]
    assert retried["values"]["approval_status"] == "succeeded" and retried["next"] == []
    assert retried["values"]["operation_id"] == case.operation_id
    assert business_snapshot(case) == committed


@pytest.mark.parametrize("change", ["disabled", "demoted"])
def test_new_process_rechecks_user_after_restart(persisted_case, change: str):
    """重启后重新获取用户，停用或降权后不能继续访问原申请。"""
    case = persisted_case
    first = run_process(case, "start", user_id=case.manager_id)
    assert first["waiting"]
    with case.factory() as session:
        manager = session.get(UserRecord, case.manager_id)
        if change == "disabled":
            manager.is_active = False
        else:
            manager.role = "agent"
        session.commit()
    before = business_snapshot(case)
    resumed = run_process(case, "resume", user_id=case.manager_id)
    expected = "ApprovalAccessError" if change == "disabled" else "ApprovalNotFoundError"
    assert resumed["error_type"] == expected and resumed["failed"]
    assert resumed["next"] == ["read_approval"]
    assert business_snapshot(case) == before
