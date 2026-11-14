"""
文件职责：首次创建独立Compose演示环境的本机随机凭据。
作用与配合：供compose.demo.yaml通过--env-file读取，.gitignore/.dockerignore排除。
边界：不修改已有.env或.env.demo，不连接数据库，不输出密码或JWT密钥。
"""

import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parents[1]


def create_demo_env() -> bool:
    """以独占创建方式写入新文件，存在时保持原样，返回是否新建。"""
    target = ROOT / ".env.demo"
    content = (
        "# 本机演示环境凭据；不要提交到Git或粘贴到聊天。\n"
        f"DEMO_POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
        f"DEMO_JWT_SECRET_KEY={secrets.token_hex(32)}\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    return True


def main() -> None:
    """仅输出文件状态，不显示凭据值。"""
    created = create_demo_env()
    print("Created .env.demo (values hidden)" if created else ".env.demo exists; left unchanged")


if __name__ == "__main__":
    main()
