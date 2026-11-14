"""
文件职责：为独立Compose演示库创建客服与主管账号。
作用与配合：.env.demo.accounts保存本机随机登录密码，Docker exec仅向演示库写哈希。
边界：不开放HTTP提权接口，不修改已有账号密码/角色/状态，不打印密码或JWT，
只接受ticketpilot-demo后端和ticketpilot_demo数据库，重复执行保持账号不变。
"""

import json
import os
from pathlib import Path
import secrets
import subprocess

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_FILE = ROOT / ".env.demo.accounts"


PROGRAM = r'''
import json, sys
from uuid import uuid4
from sqlalchemy import event, select
from ticketpilot.core.database import engine, SessionLocal
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.schemas import UserCreateRequest
from ticketpilot.identity.security import hash_password, verify_password

class DemoAccountConflictError(RuntimeError):
    pass

report={"ok":False}
writes=[]
try:
    if engine.url.database!="ticketpilot_demo" or engine.url.host!="database":
        raise RuntimeError("Unexpected demo database target")
    specifications=json.load(sys.stdin)
    if len(specifications)!=2 or [item["role"] for item in specifications]!=["agent","supervisor"]:
        raise ValueError("Unexpected demo account roles")

    @event.listens_for(engine,"before_cursor_execute")
    def count_writes(connection,cursor,statement,parameters,context,executemany):
        # 仅统计SQL类型，不收集语句正文、密码哈希或参数。
        if statement.lstrip().split()[0].upper() in ("INSERT","UPDATE","DELETE"):
            writes.append(True)

    accounts=[]
    with SessionLocal.begin() as session:
        for item in specifications:
            request=UserCreateRequest(email=item["email"],display_name=item["display_name"],password=item["password"])
            email=str(request.email).strip().lower()
            existing=session.scalar(select(UserRecord).where(UserRecord.email==email).with_for_update())
            if existing is not None:
                if existing.role!=item["role"] or not existing.is_active or not verify_password(item["password"],existing.password_hash):
                    raise DemoAccountConflictError("Existing account does not match local demo credentials")
                accounts.append({"id":existing.id,"email":email,"role":existing.role,"created":False})
                continue
            record=UserRecord(id=str(uuid4()),email=email,display_name=request.display_name,
                password_hash=hash_password(item["password"]),role=item["role"],is_active=True)
            session.add(record)
            session.flush()
            accounts.append({"id":record.id,"email":email,"role":record.role,"created":True})
    report.update(ok=True,accounts=accounts,mutating_statements=len(writes))
except Exception as error:
    # 失败时不输出输入模型、原异常正文、密码或哈希；with自动回滚。
    report.update(error_type=type(error).__name__)
finally:
    engine.dispose()
print(json.dumps(report))
raise SystemExit(0 if report["ok"] else 1)
'''


def backend_container() -> str:
    """固定项目查找后端，再用容器标签确认作用范围。"""
    command = ["docker", "compose", "--env-file", str(ROOT / ".env.demo"),
               "--project-name", "ticketpilot-demo", "-f", str(ROOT / "compose.demo.yaml")]
    result = subprocess.run(command + ["ps", "--quiet", "backend"], capture_output=True, text=True, check=True, timeout=20)
    container = result.stdout.strip()
    if not container:
        raise RuntimeError("Start the ticketpilot-demo backend first")
    label = subprocess.run(
        ["docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', container],
        capture_output=True, text=True, check=True, timeout=20,
    )
    if label.stdout.strip() != "ticketpilot-demo":
        raise RuntimeError("Unexpected container project")
    return container


def local_accounts() -> list[dict[str, str]]:
    """独占创建本机账号文件；已有文件只读取，永不重新生成密码。"""
    content = (
        "# 本机演示登录账号；由初始化脚本生成，不提交Git。\n"
        "DEMO_AGENT_EMAIL=agent.demo@example.com\n"
        f"DEMO_AGENT_PASSWORD={secrets.token_urlsafe(24)}\n"
        "DEMO_SUPERVISOR_EMAIL=supervisor.demo@example.com\n"
        f"DEMO_SUPERVISOR_PASSWORD={secrets.token_urlsafe(24)}\n"
    )
    try:
        descriptor = os.open(ACCOUNTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
    values = dotenv_values(ACCOUNTS_FILE, interpolate=False)
    result = []
    for prefix, role, name in (("AGENT", "agent", "演示客服"), ("SUPERVISOR", "supervisor", "演示主管")):
        email, password = values.get(f"DEMO_{prefix}_EMAIL"), values.get(f"DEMO_{prefix}_PASSWORD")
        if not email or not password:
            raise ValueError("Local demo account file is incomplete")
        result.append({"email": email, "password": password, "role": role, "display_name": name})
    return result


def seed_accounts(container: str, accounts: list[dict[str, str]]) -> dict:
    """凭据通过stdin发送，不放命令行或打印；只返回脱敏结果。"""
    result = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-c", PROGRAM],
        input=json.dumps(accounts), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Demo bootstrap returned no status; details suppressed")
    report = json.loads(lines[-1])
    if result.returncode or not report.get("ok"):
        raise RuntimeError("Demo bootstrap refused: " + report.get("error_type", "unknown"))
    return report


def main() -> None:
    """只输出公开邮箱、角色和创建状态，密码保留在本机文件。"""
    report = seed_accounts(backend_container(), local_accounts())
    for account in report["accounts"]:
        state = "created" if account["created"] else "unchanged"
        print(f"{account['email']} | {account['role']} | {state}")
    print("Passwords saved in .env.demo.accounts (values hidden)")


if __name__ == "__main__":
    main()
