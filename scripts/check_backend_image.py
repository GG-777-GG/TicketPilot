"""
文件职责：验证已构建后端镜像的Linux依赖、权限、迁移和HTTP启动。
作用与配合：通过Docker CLI运行唯一命名临时容器，供本地交付验收复用。
边界：不构建镜像、不连接业务库、不挂载宿主机目录/数据卷、不发布端口；
容器禁用外部网络，SQLite数据库与数据探针仅存在于临时容器中。
"""

import argparse
import subprocess
import sys
from uuid import uuid4


IMPORT_CHECK = r'''
import os, platform, subprocess, sys
from pathlib import Path
import cv2, onnxruntime, pypdfium2
from rapidocr import RapidOCR
assert platform.system() == "Linux"
assert os.getuid() == 10001 and os.getgid() == 10001
assert sys.executable.startswith("/app/.venv/bin/")
assert not Path("/app/.env").exists() and not Path("/app/.git").exists()
assert not Path("/app/.venv/Scripts").exists()
assert Path("/app/migrations/env.py").is_file()
subprocess.run(["uv", "--cache-dir", "/tmp/uv-image-check", "pip", "check"], check=True, capture_output=True, text=True)
probe = Path("/app/data/.image-check")
probe.write_text("temporary image check", encoding="utf-8")
probe.unlink()
# 外部网络已禁用，验证随依赖安装的OCR模型可由非root用户离线初始化。
engine = RapidOCR()
assert engine is not None
print("Linux UID/GID 10001, writable data, native imports and offline OCR: OK")
'''


MIGRATION_CHECK = r'''
import os
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
url = os.environ["DATABASE_URL"]
assert url == "sqlite:////tmp/ticketpilot-image-check.sqlite"
config = Config("/app/alembic.ini")
config.attributes["database_url"] = url
command.upgrade(config, "head")
engine = create_engine(url)
try:
    tables = set(inspect(engine).get_table_names())
    assert {"users", "tickets", "audit_events", "approval_requests"}.issubset(tables)
    with engine.connect() as connection:
        actual = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert actual == ScriptDirectory.from_config(config).get_current_head()
finally:
    engine.dispose()
print("Packaged Alembic migrations on isolated SQLite: OK")
'''


HTTP_CHECK = r'''
import json, time
from urllib.request import ProxyHandler, build_opener
from uuid import UUID
opener = build_opener(ProxyHandler({}))
deadline = time.monotonic() + 30
while True:
    try:
        with opener.open("http://127.0.0.1:8000/health", timeout=2) as response:
            payload = json.load(response)
            request_id = response.headers["X-Request-ID"]
            trace_id = response.headers["X-Trace-ID"]
        break
    except OSError:
        if time.monotonic() >= deadline:
            raise RuntimeError("Image health endpoint did not become ready") from None
        time.sleep(0.25)
assert payload == {"status": "ok", "service": "TicketPilot"}
assert UUID(request_id) and len(trace_id) == 32 and int(trace_id, 16) != 0
with opener.open("http://127.0.0.1:8000/openapi.json", timeout=5) as response:
    paths = json.load(response)["paths"]
assert {"/health", "/v1/agent/chat", "/v1/approvals", "/v1/audit-events"}.issubset(paths)
print("Health, OpenAPI, X-Request-ID and real X-Trace-ID: OK")
'''


def docker(arguments: list[str], *, source: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """参数列表调用Docker，不拼接shell；失败时仅显示测试步骤输出。"""
    result = subprocess.run(
        ["docker", *arguments], input=source, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result


def check_image(image: str) -> None:
    """仅操作本函数生成的唯一容器名，所有检查结束后清理。"""
    name = "ticketpilot-image-check-" + uuid4().hex
    attempted = False
    try:
        attempted = True
        docker([
            "run", "--detach", "--name", name, "--network", "none",
            "--env", "DATABASE_URL=sqlite:////tmp/ticketpilot-image-check.sqlite",
            image,
        ])
        print("Temporary container started: no published ports, mounts or external network", flush=True)
        for title, source, timeout in (
            ("Linux dependencies and non-root permissions", IMPORT_CHECK, 120),
            ("Isolated database migrations", MIGRATION_CHECK, 90),
            ("HTTP application and request/trace correlation", HTTP_CHECK, 45),
        ):
            print("Checking: " + title, flush=True)
            result = docker(["exec", "-i", name, "python", "-"], source=source, timeout=timeout)
            # 库初始化及迁移输出留在捕获结果中，正常时只显示最后一行摘要。
            lines = result.stdout.strip().splitlines()
            print(lines[-1] if lines else "OK", flush=True)
    finally:
        if attempted:
            result = subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=20,
            )
            if result.returncode == 0:
                print("Temporary container removed", flush=True)
            elif sys.exc_info()[0] is None:
                raise RuntimeError("Temporary container cleanup failed: " + name)
    print("Backend image check passed", flush=True)


def main() -> None:
    """读取镜像标签；默认使用本项目隔离验证镜像。"""
    parser = argparse.ArgumentParser(description="Check backend image without business data")
    parser.add_argument("--image", default="ticketpilot-backend:interview-check")
    arguments = parser.parse_args()
    check_image(arguments.image)


if __name__ == "__main__":
    main()
