"""
文件职责：在独立演示PostgreSQL中新建临时库，验证镜像内真实API/审批/MCP链路。
作用与配合：compose.demo.yaml运行后，通过后端容器执行；模型和向量服务使用替身。
边界：只操作ticketpilot-demo项目；不改演示业务行或原业务栈，不调用真实模型，
不输出密码/JWT；临时库和检查点在结束时清理。
"""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


PROGRAM = r'''
import json, os, re, secrets, tempfile
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

report = {"ok": False}
stage = "validate-demo-target"
admin = None
created = False
database = "tp_check_" + uuid4().hex
project_engine = None
try:
    base_url = make_url(os.environ["DATABASE_URL"])
    if base_url.database != "ticketpilot_demo" or base_url.host != "database":
        raise RuntimeError("Unexpected demo database target")
    assert re.fullmatch(r"tp_check_[0-9a-f]{32}", database)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    stage = "create-temporary-postgres-database"
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + database + '"'))
    created = True
    # 只修改本次docker exec子进程的环境；运行中的后端仍使用演示库。
    target_url = base_url.set(database=database)
    os.environ["DATABASE_URL"] = target_url.render_as_string(hide_password=False)

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from unittest.mock import MagicMock
    from ticketpilot.core.database import engine, SessionLocal
    from ticketpilot.core.config import settings
    from ticketpilot.identity.models import UserRecord
    from ticketpilot.identity.security import create_access_token, hash_password
    from ticketpilot.tickets.models import TicketRecord
    from ticketpilot.approvals.models import ApprovalExecutionRecord
    from ticketpilot.agent.model import FakeAgentModel
    from ticketpilot.agent.schemas import ToolCallDecision, ToolRequest, FinalAnswerDecision
    from ticketpilot.agent.dependencies import get_agent_model
    from ticketpilot.agent import dispatcher
    from ticketpilot.knowledge.api.dependencies import get_embedding_provider
    from ticketpilot.knowledge.embeddings.base import EmbeddingProvider
    from ticketpilot.app import create_app
    project_engine = engine
    if engine.url.database != database:
        raise RuntimeError("Temporary database configuration was not applied")
    stage = "migrate-temporary-postgres-database"
    config = Config("/app/alembic.ini")
    config.attributes["database_url"] = target_url.render_as_string(hide_password=False)
    command.upgrade(config, "head")

    stage = "seed-temporary-records"
    owner_id, manager_id, ticket_id = [str(uuid4()) for _ in range(3)]
    password_hash = hash_password(secrets.token_urlsafe(24))
    with SessionLocal.begin() as session:
        session.add_all([
            UserRecord(id=owner_id,email="owner@example.com",display_name="Test owner",password_hash=password_hash,role="agent",is_active=True),
            UserRecord(id=manager_id,email="manager@example.com",display_name="Test manager",password_hash=password_hash,role="supervisor",is_active=True),
            TicketRecord(id=ticket_id,customer_name="Test",contact_email="test@example.com",channel="web",subject="Container PG validation",description="Synthetic test record",priority="medium",status="open"),
        ])
    owner_headers = {"Authorization": "Bearer " + create_access_token(owner_id)}
    manager_headers = {"Authorization": "Bearer " + create_access_token(manager_id)}
    model = FakeAgentModel([
        ToolCallDecision(kind="tool_call",tool_request=ToolRequest(name="get_ticket_details",arguments={"ticket_id":ticket_id})),
        FinalAnswerDecision(kind="final_answer",answer="Test ticket closed."),
    ])
    exporter = InMemorySpanExporter()
    app = create_app(span_exporter=exporter)
    app.dependency_overrides[get_agent_model] = lambda: model
    app.dependency_overrides[get_embedding_provider] = lambda: MagicMock(spec=EmbeddingProvider)
    settings.enable_evidence_verification = False

    def forbidden_local_query(*args, **kwargs):
        raise AssertionError("Parent must use actual MCP subprocess")

    original_query = dispatcher.ticket_tools.get_ticket_details
    dispatcher.ticket_tools.get_ticket_details = forbidden_local_query
    try:
        with tempfile.TemporaryDirectory(prefix="ticketpilot-pg-check-") as directory:
            settings.approval_workflow_checkpoint_path = Path(directory) / "workflow.sqlite"
            with TestClient(app) as client:
                stage = "approval-create-and-permission-denial"
                response = client.post("/v1/approvals",headers=owner_headers,json={"ticket_id":ticket_id,"request_reason":"Synthetic validation"})
                assert response.status_code == 201
                approval_id = response.json()["id"]
                base = "/v1/approvals/" + approval_id
                assert client.post(base+"/review",headers=owner_headers,json={"approved":True,"reason":"Test"}).status_code == 403
                assert client.post(base+"/workflow/run",headers=owner_headers,json={"action":"start"}).status_code == 200
                stage = "approval-review-workflow-execution-and-replay"
                assert client.post(base+"/review",headers=manager_headers,json={"approved":True,"reason":"Verified"}).status_code == 200
                resumed = client.post(base+"/workflow/run",headers=manager_headers,json={"action":"resume"})
                assert resumed.status_code == 200 and resumed.json()["approval"]["status"] == "succeeded"
                assert client.post(base+"/execute",headers=manager_headers).status_code == 200
                stage = "audit-and-single-execution-receipt"
                audit = client.get("/v1/audit-events",headers=manager_headers,params={"approval_id":approval_id})
                assert audit.status_code == 200
                rows = audit.json()["items"]
                assert len(rows) == 5
                assert {row["event_type"] for row in rows} == {"approval.requested","approval.denied","approval.approved","ticket.closed","execution.replayed"}
                assert all(row["request_id"] and row["trace_id"] for row in rows)
                with SessionLocal() as session:
                    assert session.get(TicketRecord,ticket_id).status == "closed"
                    assert session.query(ApprovalExecutionRecord).count() == 1
                stage = "real-stdio-mcp-and-six-span-trace"
                chat = client.post("/v1/agent/chat",headers=owner_headers,json={"message":"Query test ticket "+ticket_id,"mode":"auto"})
                assert chat.status_code == 200
                tool_message = next(message for message in model.received_messages[1] if message.role == "tool")
                result = json.loads(tool_message.content)
                assert result["ticket"]["id"] == ticket_id and result["ticket"]["status"] == "closed"
                spans = [span for span in exporter.get_finished_spans() if format(span.context.trace_id,"032x") == chat.headers["X-Trace-ID"]]
                assert len(spans) == 6
                assert {span.name for span in spans if span.name in ("agent.run","model.decide","tool.execute","mcp.client")} == {"agent.run","model.decide","tool.execute","mcp.client"}
    finally:
        dispatcher.ticket_tools.get_ticket_details = original_query
        app.dependency_overrides.clear()
    report.update(ok=True, migration=True, approval_and_rbac=True, workflow_and_idempotency=True, audit=True, real_stdio_mcp=True, six_span_trace=True)
except Exception as error:
    # 仅暴露步骤和异常类型，不打印原URL、响应正文、密码或令牌。
    report.update(stage=stage,error_type=type(error).__name__)
finally:
    if project_engine is not None:
        project_engine.dispose()
    if created and admin is not None:
        try:
            assert re.fullmatch(r"tp_check_[0-9a-f]{32}", database)
            with admin.connect() as connection:
                connection.execute(text('DROP DATABASE "' + database + '" WITH (FORCE)'))
            report["temporary_database_removed"] = True
        except Exception as error:
            report.update(ok=False,cleanup_error_type=type(error).__name__,temporary_database=database)
    if admin is not None:
        admin.dispose()
print(json.dumps(report))
raise SystemExit(0 if report["ok"] else 1)
'''


def main() -> None:
    """定位固定演示项目的后端，输入脚本但不把凭据放进命令行。"""
    base = ["docker", "compose", "--env-file", str(ROOT / ".env.demo"),
            "--project-name", "ticketpilot-demo", "-f", str(ROOT / "compose.demo.yaml")]
    result = subprocess.run(base + ["ps", "--quiet", "backend"], capture_output=True, text=True, timeout=20, check=True)
    container = result.stdout.strip()
    if not container:
        raise RuntimeError("Start the ticketpilot-demo backend first")
    label = subprocess.run(
        ["docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', container],
        capture_output=True, text=True, timeout=20, check=True,
    )
    if label.stdout.strip() != "ticketpilot-demo":
        raise RuntimeError("Unexpected container project")
    print("Checking temporary PostgreSQL database, API and real MCP; values hidden", flush=True)
    result = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-"], input=PROGRAM,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    # Alembic和第三方库日志不直接转发，只显示最后一行脱敏JSON。
    lines = result.stdout.strip().splitlines()
    print(lines[-1] if lines else "Validation ended without a status report", flush=True)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
