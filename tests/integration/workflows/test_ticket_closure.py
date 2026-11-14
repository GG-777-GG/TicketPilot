"""
文件职责：验证关闭工单工作流的读取、审批等待、执行及故障恢复。
作用与配合：运行学习者编写的真实图，复用审批查询服务与临时SQLite。
边界：只操作临时SQLite与内存检查点，不验证HTTP认证或真实业务库。
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.workflows.ticket_closure import graph as closure
from ticketpilot.approvals import execution, repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalReviewRequest
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def workflow():
    """每项测试独立保存检查点；同一场景的恢复沿用会话编号。"""
    return SimpleNamespace(
        graph=closure.build_workflow(InMemorySaver()),
        config={"configurable": {"thread_id": str(uuid4())}},
    )


def invoke_as(session: Session, workflow, user_id: str, graph_input, *, config=None):
    """像实际请求一样每次新建Session，并重新读取当前用户后调用图。"""
    with Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False) as current:
        viewer = current.get(UserRecord, user_id)
        assert viewer is not None
        return workflow.graph.invoke(
            graph_input, config=config or workflow.config,
            context=closure.TicketClosureContext(session=current, viewer=viewer),
        )


@pytest.fixture
def closure_context(database_session: Session):
    """提交父记录与待审批申请，每项测试获得独立数据库。"""
    owner = build_user(role="agent")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="customer@example.com",
        channel="web", subject="申请关闭", description="问题已解决",
        priority="medium", status="open",
    )
    database_session.add_all([owner, ticket])
    database_session.flush()
    record = ApprovalRecord(
        id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id,
        requested_by=owner.id, request_reason="申请关闭",
    )
    database_session.add(record)
    database_session.commit()
    return SimpleNamespace(owner=owner, ticket=ticket, record=record)


@pytest.fixture
def approved_context(database_session: Session, closure_context):
    """调用真实审核服务准备获批申请，保留普通申请人与管理执行人。"""
    ctx = closure_context
    ctx.manager = build_user(role="supervisor")
    database_session.add(ctx.manager)
    database_session.commit()
    service.review_approval(
        session=database_session, approval_id=ctx.record.id,
        request=ApprovalReviewRequest(approved=True, reason="已核验问题解决"),
        reviewer=ctx.manager,
    )
    return ctx


def assert_execution_saved(session: Session, ctx, result, executor_id: str) -> None:
    """从新Session核对工单、申请、唯一成功记录及图输出一致。"""
    with Session(bind=session.get_bind()) as verification:
        record = verification.get(ApprovalRecord, ctx.record.id)
        ticket = verification.get(TicketRecord, ctx.ticket.id)
        receipts = verification.scalars(select(ApprovalExecutionRecord)).all()
        assert ticket.status == "closed" and record.status == "succeeded"
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.operation_id == record.operation_id == result["operation_id"]
        assert receipt.ticket_id == ticket.id == result["ticket_id"]
        assert receipt.executed_by == executor_id
        assert receipt.action == record.action == "close_ticket"
        assert receipt.result == record.result == result["result"]
        assert result["approval_id"] == record.id and result["approval_status"] == "succeeded"
        assert not result.get("__interrupt__")


def initial_state(approval_id: str) -> closure.TicketClosureState:
    """故意提供过时或错误的数据，验证读取节点以数据库记录为准。"""
    return {
        "approval_id": approval_id,
        "operation_id": str(uuid4()),
        "ticket_id": str(uuid4()),
        "approval_status": "approved",
        "result": "客户端自报的成功结果",
    }


def business_snapshot(session: Session) -> dict[str, list[dict]]:
    """检查工单、申请和执行记录的全部列，不仅检查返回提示。"""
    return {
        model.__tablename__: [dict(row) for row in session.execute(select(model.__table__)).mappings()]
        for model in (TicketRecord, ApprovalRecord, ApprovalExecutionRecord)
    }


@pytest.mark.parametrize("status", ["pending", "rejected", "executing", "succeeded", "failed"])
def test_read_uses_database_values_without_writing(database_session: Session, closure_context, workflow, status: str) -> None:
    """读取各种已有状态均不执行；错误输入被覆盖，输出可转JSON。"""
    ctx = closure_context
    # 这里只准备查询样本，不宣称这些状态都能由当前执行服务产生。
    ctx.record.status = status
    ctx.record.result = "数据库中的执行结果" if status == "succeeded" else None
    database_session.commit()
    before = business_snapshot(database_session)

    result = workflow.graph.invoke(
        initial_state(ctx.record.id),
        config=workflow.config,
        context=closure.TicketClosureContext(session=database_session, viewer=ctx.owner),
    )

    saved_state = workflow.graph.get_state(workflow.config).values
    assert saved_state == {
        "approval_id": ctx.record.id,
        "operation_id": ctx.record.operation_id,
        "ticket_id": ctx.ticket.id,
        "approval_status": status,
        "result": ctx.record.result or "",
    }
    # 输出仅有普通数据，未混入Session、用户或ORM对象。
    assert json.loads(json.dumps(saved_state)) == saved_state
    if status == "pending":
        assert result["__interrupt__"][0].value["approval_id"] == ctx.record.id
        assert workflow.graph.get_state(workflow.config).next == ("wait_for_approval",)
    else:
        assert not result.get("__interrupt__")
        assert workflow.graph.get_state(workflow.config).next == ()
    assert business_snapshot(database_session) == before
    assert not database_session.new and not database_session.dirty and not database_session.deleted
    # 再用独立Session核对本阶段不进入执行分支的样本。
    with Session(bind=database_session.get_bind()) as verification:
        assert business_snapshot(verification) == before


@pytest.mark.parametrize("resume_value", [
    True,
    {"approved": True},
    {"approved": True, "approval_id": str(uuid4()), "ticket_id": str(uuid4())},
])
def test_resume_input_cannot_approve_or_replace_target(database_session: Session, closure_context, workflow, resume_value) -> None:
    """恢复数据不能批准或替换目标，未审核申请每次复查后仍暂停。"""
    ctx = closure_context
    before = business_snapshot(database_session)
    first = invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))
    assert first["__interrupt__"]
    operation_id = first["operation_id"]

    for _ in range(2):
        resumed = invoke_as(database_session, workflow, ctx.owner.id, Command(resume=resume_value))
        assert resumed["__interrupt__"]
        assert resumed["approval_status"] == "pending"
        assert resumed["approval_id"] == ctx.record.id
        assert resumed["ticket_id"] == ctx.ticket.id
        assert resumed["operation_id"] == operation_id
        assert resumed["result"] == ""
        assert workflow.graph.get_state(workflow.config).next == ("wait_for_approval",)

    assert business_snapshot(database_session) == before


@pytest.mark.parametrize("approved", [True, False])
def test_resume_reads_actual_review_and_executes_only_when_approved(database_session: Session, closure_context, workflow, approved: bool) -> None:
    """真实批准后由管理角色恢复执行；拒绝后结束且业务数据不变。"""
    ctx = closure_context
    reviewer = build_user(role="supervisor")
    database_session.add(reviewer)
    database_session.commit()
    paused = invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))
    assert paused["__interrupt__"]

    with Session(bind=database_session.get_bind(), expire_on_commit=False) as review_session:
        service.review_approval(
            session=review_session, approval_id=ctx.record.id,
            request=ApprovalReviewRequest(approved=approved, reason="实际审核决定"),
            reviewer=review_session.get(UserRecord, reviewer.id),
        )
    before_resume = business_snapshot(database_session)

    resumed = invoke_as(database_session, workflow, reviewer.id, Command(resume={"approved": not approved}))

    assert resumed["operation_id"] == paused["operation_id"]
    assert not resumed.get("__interrupt__")
    assert workflow.graph.get_state(workflow.config).next == ()
    if approved:
        assert_execution_saved(database_session, ctx, resumed, reviewer.id)
    else:
        assert resumed["approval_status"] == "rejected" and resumed["result"] == ""
        assert business_snapshot(database_session) == before_resume


@pytest.mark.parametrize("change", ["disabled", "unknown_role", "other_agent", "demoted_supervisor"])
def test_resume_rechecks_current_user_scope(database_session: Session, closure_context, workflow, change: str) -> None:
    """等待前能查询，不代表恢复后仍有权限；每次按当前身份检查。"""
    ctx = closure_context
    starting_user = ctx.owner
    if change == "demoted_supervisor":
        starting_user = build_user(role="supervisor")
        database_session.add(starting_user)
        database_session.commit()
    first = invoke_as(database_session, workflow, starting_user.id, initial_state(ctx.record.id))
    assert first["__interrupt__"]

    resumed_user = starting_user
    if change == "disabled":
        resumed_user.is_active = False
    elif change == "unknown_role":
        resumed_user.role = "unknown"
    elif change == "other_agent":
        resumed_user = build_user(role="agent")
        database_session.add(resumed_user)
    else:
        resumed_user.role = "agent"
    database_session.commit()
    before_resume = business_snapshot(database_session)
    expected_error = service.ApprovalAccessError if change in ("disabled", "unknown_role") else service.ApprovalNotFoundError

    with pytest.raises(expected_error):
        invoke_as(database_session, workflow, resumed_user.id, Command(resume={"recheck": True}))

    assert workflow.graph.get_state(workflow.config).values["approval_status"] == "pending"
    assert business_snapshot(database_session) == before_resume


def test_resume_detects_missing_approval(database_session: Session, closure_context, workflow) -> None:
    """暂停期间申请被移除，恢复复查必须报错，不能继续使用快照。"""
    ctx = closure_context
    assert invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))["__interrupt__"]
    database_session.delete(ctx.record)
    database_session.commit()
    before_resume = business_snapshot(database_session)

    with pytest.raises(service.ApprovalNotFoundError):
        invoke_as(database_session, workflow, ctx.owner.id, Command(resume={"recheck": True}))

    assert business_snapshot(database_session) == before_resume


def test_failed_recheck_can_retry_with_same_checkpoint(database_session: Session, closure_context, workflow, monkeypatch) -> None:
    """恢复后的查询失败可继续重试，仍沿用原申请和操作编号。"""
    ctx = closure_context
    before = business_snapshot(database_session)
    first = invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))
    assert first["__interrupt__"]

    def fail_query(*args, **kwargs):
        """模拟恢复复查中的存储故障。"""
        raise SQLAlchemyError("模拟复查失败")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "get_approval_by_id", fail_query)
        with pytest.raises(service.ApprovalStorageError):
            invoke_as(database_session, workflow, ctx.owner.id, Command(resume={"recheck": True}))

    failed = workflow.graph.get_state(workflow.config)
    assert failed.next == ("read_approval",)
    assert any(task.error for task in failed.tasks)
    # 等待节点已完成，失败的是查询节点；None继续失败步骤，不再提交审批输入。
    retried = invoke_as(database_session, workflow, ctx.owner.id, None)
    assert retried["__interrupt__"] and retried["approval_status"] == "pending"
    assert retried["operation_id"] == first["operation_id"]
    assert business_snapshot(database_session) == before


def test_thread_ids_keep_pending_approvals_separate(database_session: Session, closure_context, workflow) -> None:
    """同一张图运行两条会话，只恢复指定申请，不改动另一条检查点。"""
    ctx = closure_context
    another = ApprovalRecord(
        id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ctx.ticket.id,
        requested_by=ctx.owner.id, request_reason="另一份申请",
    )
    reviewer = build_user(role="admin")
    database_session.add_all([another, reviewer])
    database_session.commit()
    other_config = {"configurable": {"thread_id": str(uuid4())}}
    assert invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))["__interrupt__"]
    assert invoke_as(database_session, workflow, ctx.owner.id, initial_state(another.id), config=other_config)["__interrupt__"]
    service.review_approval(
        session=database_session, approval_id=ctx.record.id,
        request=ApprovalReviewRequest(approved=False, reason="拒绝第一份"), reviewer=reviewer,
    )
    before_resume = business_snapshot(database_session)

    completed = invoke_as(database_session, workflow, ctx.owner.id, Command(resume={"recheck": True}))
    other = workflow.graph.get_state(other_config)

    assert completed["approval_status"] == "rejected"
    assert not completed.get("__interrupt__")
    assert other.values["approval_id"] == another.id
    assert other.values["operation_id"] == another.operation_id
    assert other.values["approval_status"] == "pending"
    assert other.next == ("wait_for_approval",) and any(task.interrupts for task in other.tasks)
    assert business_snapshot(database_session) == before_resume
    with Session(bind=database_session.get_bind()) as verification:
        assert business_snapshot(verification) == before_resume


@pytest.mark.parametrize("role, own", [
    ("agent", True), ("agent", False),
    ("supervisor", True), ("supervisor", False),
    ("admin", True), ("admin", False),
])
def test_workflow_preserves_query_scope(database_session: Session, closure_context, workflow, role: str, own: bool) -> None:
    """即使他人申请已在Session缓存中，普通客服仍不能读取。"""
    ctx = closure_context
    viewer = ctx.owner if own else build_user(role=role)
    viewer.role = role
    database_session.add(viewer)
    database_session.commit()
    before = business_snapshot(database_session)
    graph = workflow.graph
    context = closure.TicketClosureContext(session=database_session, viewer=viewer)

    if role == "agent" and not own:
        with pytest.raises(service.ApprovalNotFoundError, match="审批申请不存在"):
            graph.invoke(initial_state(ctx.record.id), config=workflow.config, context=context)
    else:
        result = graph.invoke(initial_state(ctx.record.id), config=workflow.config, context=context)
        assert result["approval_id"] == ctx.record.id

    assert business_snapshot(database_session) == before


@pytest.mark.parametrize("role, active", [("agent", False), ("unknown", True)])
def test_workflow_rejects_disallowed_user(database_session: Session, closure_context, workflow, role: str, active: bool) -> None:
    """Context提供用户对象也必须经过Service的角色和启用状态检查。"""
    ctx = closure_context
    ctx.owner.role, ctx.owner.is_active = role, active
    database_session.commit()
    before = business_snapshot(database_session)

    with pytest.raises(service.ApprovalAccessError):
        workflow.graph.invoke(
            initial_state(ctx.record.id),
            config=workflow.config,
            context=closure.TicketClosureContext(session=database_session, viewer=ctx.owner),
        )

    assert business_snapshot(database_session) == before


def test_missing_approval_propagates_business_error(database_session: Session, closure_context, workflow) -> None:
    """申请不存在时不伪造成功结果，由调用层处理业务异常。"""
    before = business_snapshot(database_session)
    with pytest.raises(service.ApprovalNotFoundError, match="审批申请不存在"):
        workflow.graph.invoke(
            initial_state(str(uuid4())),
            config=workflow.config,
            context=closure.TicketClosureContext(session=database_session, viewer=closure_context.owner),
        )
    assert business_snapshot(database_session) == before


def test_storage_error_propagates_and_session_can_be_reused(database_session: Session, closure_context, workflow, monkeypatch) -> None:
    """模拟查询故障，Service回滚后仍保留异常链，后续调用可以成功。"""
    ctx = closure_context
    before = business_snapshot(database_session)

    def fail_query(*args, **kwargs):
        """只替换查询故障，不替换实际业务权限判断。"""
        raise SQLAlchemyError("模拟数据库查询失败")

    graph = workflow.graph
    context = closure.TicketClosureContext(session=database_session, viewer=ctx.owner)
    with monkeypatch.context() as patch:
        patch.setattr(repository, "get_approval_by_id", fail_query)
        with pytest.raises(service.ApprovalStorageError) as captured:
            graph.invoke(initial_state(ctx.record.id), config=workflow.config, context=context)

    assert isinstance(captured.value.__cause__, SQLAlchemyError)
    result = graph.invoke(None, config=workflow.config, context=context)
    assert result["approval_status"] == "pending"
    assert business_snapshot(database_session) == before


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("ticket_status", ["open", "in_progress", "resolved"])
def test_approved_graph_closes_only_saved_target(database_session: Session, approved_context, workflow, role: str, ticket_status: str) -> None:
    """管理角色执行三种可关闭状态，忽略输入里的其他工单和操作编号。"""
    ctx = approved_context
    ctx.manager.role, ctx.ticket.status = role, ticket_status
    other = TicketRecord(
        id=str(uuid4()), customer_name="其他客户", contact_email="other@example.com",
        channel="web", subject="不应关闭", description="其他问题", priority="medium", status="open",
    )
    database_session.add(other)
    database_session.commit()
    before = business_snapshot(database_session)
    supplied = initial_state(ctx.record.id)
    supplied["ticket_id"] = other.id

    result = invoke_as(database_session, workflow, ctx.manager.id, supplied)

    assert_execution_saved(database_session, ctx, result, ctx.manager.id)
    assert result["operation_id"] != supplied["operation_id"]
    assert json.loads(json.dumps(result)) == result
    assert workflow.graph.get_state(workflow.config).next == ()
    after = business_snapshot(database_session)
    assert next(row for row in after["tickets"] if row["id"] == other.id) == next(row for row in before["tickets"] if row["id"] == other.id)
    # 已完成会话再次读取，不创建新执行或更新时间。
    replay = invoke_as(database_session, workflow, ctx.manager.id, None)
    assert replay == result and business_snapshot(database_session) == after


def test_owner_cannot_execute_but_manager_can_retry(database_session: Session, approved_context, workflow) -> None:
    """能查看自己的获批申请，不代表能执行；管理角色可继续原失败节点。"""
    ctx = approved_context
    before = business_snapshot(database_session)
    with pytest.raises(service.ApprovalAccessError):
        invoke_as(database_session, workflow, ctx.owner.id, initial_state(ctx.record.id))
    failed = workflow.graph.get_state(workflow.config)
    assert failed.next == ("execute_ticket_closure",)
    assert failed.values["approval_status"] == "approved"
    assert business_snapshot(database_session) == before

    result = invoke_as(database_session, workflow, ctx.manager.id, None)

    assert_execution_saved(database_session, ctx, result, ctx.manager.id)


@pytest.mark.parametrize("failure_point", ["after_close", "after_receipt"])
def test_execution_failure_rolls_back_and_retries_node(database_session: Session, approved_context, workflow, monkeypatch, failure_point: str) -> None:
    """真实UPDATE或flush之后失败，整体回滚并从执行节点恢复。"""
    ctx = approved_context
    before = business_snapshot(database_session)
    target = "close_ticket_for_approval" if failure_point == "after_close" else "add_execution"
    real_operation = getattr(repository, target)

    def write_then_fail(*args, **kwargs):
        """先执行实际SQL，再触发错误，验证回滚而非写入前拒绝。"""
        real_operation(*args, **kwargs)
        raise SQLAlchemyError("模拟执行事务失败")

    with monkeypatch.context() as patch:
        patch.setattr(repository, target, write_then_fail)
        with pytest.raises(service.ApprovalStorageError):
            invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))

    failed = workflow.graph.get_state(workflow.config)
    assert failed.next == ("execute_ticket_closure",)
    assert failed.values["operation_id"] == ctx.record.operation_id
    assert business_snapshot(database_session) == before
    result = invoke_as(database_session, workflow, ctx.manager.id, None)
    assert_execution_saved(database_session, ctx, result, ctx.manager.id)


def test_node_error_after_commit_reuses_receipt(database_session: Session, approved_context, workflow, monkeypatch) -> None:
    """业务已提交但图状态未更新，重试不得再关闭或改变原执行人。"""
    ctx = approved_context
    another_manager = build_user(role="admin")
    database_session.add(another_manager)
    database_session.commit()
    real_node = closure.execute_ticket_closure
    real_close = repository.close_ticket_for_approval
    fail_once = True
    close_calls = []

    def count_close(*args, **kwargs):
        """统计实际关闭尝试，检查重试没有重新执行工单UPDATE。"""
        close_calls.append(True)
        return real_close(*args, **kwargs)

    def fail_after_node(state, runtime: Runtime[closure.TicketClosureContext]):
        """运行完整节点和业务提交后，再模拟返回结果之前报错。"""
        nonlocal fail_once
        result = real_node(state, runtime)
        if fail_once:
            fail_once = False
            raise RuntimeError("模拟业务提交后节点返回失败")
        return result

    monkeypatch.setattr(repository, "close_ticket_for_approval", count_close)
    monkeypatch.setattr(closure, "execute_ticket_closure", fail_after_node)
    # 必须替换后再建图，使图注册故障节点。
    workflow.graph = closure.build_workflow(InMemorySaver())
    with pytest.raises(RuntimeError, match="提交后节点返回失败"):
        invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))

    failed = workflow.graph.get_state(workflow.config)
    assert failed.next == ("execute_ticket_closure",)
    assert failed.values["approval_status"] == "approved"
    committed = business_snapshot(database_session)
    assert committed["approval_requests"][0]["status"] == "succeeded"
    assert committed["tickets"][0]["status"] == "closed"
    assert len(committed["approval_executions"]) == 1 and len(close_calls) == 1

    result = invoke_as(database_session, workflow, another_manager.id, None)

    assert_execution_saved(database_session, ctx, result, ctx.manager.id)
    assert business_snapshot(database_session) == committed and len(close_calls) == 1
    assert workflow.graph.get_state(workflow.config).next == ()


def test_commit_acknowledgement_error_can_resume(database_session: Session, approved_context, workflow, monkeypatch) -> None:
    """commit实际成功后报告存储错误，图重试仍复用原操作结果。"""
    ctx = approved_context
    real_execute = execution.execute_approval

    def execute_with_lost_acknowledgement(*, session, approval_id, executor):
        """只改本次Session的commit，真实提交后制造调用方错误。"""
        real_commit = session.commit

        def commit_then_fail():
            """提交生效，但调用方收到异常。"""
            real_commit()
            raise SQLAlchemyError("模拟提交响应丢失")

        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", commit_then_fail)
            return real_execute(session=session, approval_id=approval_id, executor=executor)

    with monkeypatch.context() as patch:
        patch.setattr(execution, "execute_approval", execute_with_lost_acknowledgement)
        with pytest.raises(service.ApprovalStorageError):
            invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))

    committed = business_snapshot(database_session)
    assert committed["approval_requests"][0]["status"] == "succeeded"
    assert len(committed["approval_executions"]) == 1
    assert workflow.graph.get_state(workflow.config).next == ("execute_ticket_closure",)
    result = invoke_as(database_session, workflow, ctx.manager.id, None)
    assert_execution_saved(database_session, ctx, result, ctx.manager.id)
    assert business_snapshot(database_session) == committed


@pytest.mark.parametrize("change", ["disabled", "unknown_role", "demoted"])
def test_execution_retry_checks_fresh_executor(database_session: Session, approved_context, workflow, monkeypatch, change: str) -> None:
    """执行节点恢复时也检查最新用户，不能沿用上次获准的管理身份。"""
    ctx = approved_context
    real_node = closure.execute_ticket_closure
    fail_once = True

    def fail_before_execution(state, runtime: Runtime[closure.TicketClosureContext]):
        """先制造未执行的失败检查点，后续调用进入真实节点。"""
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("模拟执行前节点故障")
        return real_node(state, runtime)

    monkeypatch.setattr(closure, "execute_ticket_closure", fail_before_execution)
    workflow.graph = closure.build_workflow(InMemorySaver())
    with pytest.raises(RuntimeError, match="执行前节点故障"):
        invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))
    if change == "disabled":
        ctx.manager.is_active = False
    else:
        ctx.manager.role = "unknown" if change == "unknown_role" else "agent"
    database_session.commit()
    before = business_snapshot(database_session)

    with pytest.raises(service.ApprovalAccessError):
        invoke_as(database_session, workflow, ctx.manager.id, None)

    assert business_snapshot(database_session) == before
    assert workflow.graph.get_state(workflow.config).next == ("execute_ticket_closure",)


def test_changed_ticket_prevents_execution_without_receipt(database_session: Session, approved_context, workflow) -> None:
    """审批后工单已被其他操作关闭，图执行冲突且申请抢占被回滚。"""
    ctx = approved_context
    ctx.ticket.status = "closed"
    database_session.commit()
    before = business_snapshot(database_session)
    with pytest.raises(service.ApprovalConflictError):
        invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))
    assert business_snapshot(database_session) == before
    assert workflow.graph.get_state(workflow.config).next == ("execute_ticket_closure",)


@pytest.mark.parametrize("missing_field", ["reviewed_by", "reviewed_at"])
def test_incomplete_approval_cannot_execute(database_session: Session, approved_context, workflow, missing_field: str) -> None:
    """仅有approved文字不足以执行，正式服务仍检查真实审批信息。"""
    ctx = approved_context
    setattr(ctx.record, missing_field, None)
    database_session.commit()
    before = business_snapshot(database_session)
    with pytest.raises(service.ApprovalConflictError):
        invoke_as(database_session, workflow, ctx.manager.id, initial_state(ctx.record.id))
    assert business_snapshot(database_session) == before


def test_execution_node_does_not_trust_approved_state(database_session: Session, closure_context) -> None:
    """即使直接调用节点且State写approved，Service也拒绝数据库中的pending。"""
    ctx = closure_context
    manager = build_user(role="supervisor")
    database_session.add(manager)
    database_session.commit()
    before = business_snapshot(database_session)
    with pytest.raises(service.ApprovalConflictError):
        closure.execute_ticket_closure(
            initial_state(ctx.record.id),
            Runtime(context=closure.TicketClosureContext(session=database_session, viewer=manager)),
        )
    assert business_snapshot(database_session) == before
