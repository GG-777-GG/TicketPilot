"""
真实 Agent 完整链路冒烟测试。

业务目标：
使用真实 Ollama 和临时数据库，验证 Agent 能够根据用户问题
调用工单工具，并根据工具结果生成最终回答。

执行流程：
1. 创建临时的内存 SQLite 数据库。
2. 根据 ORM 模型创建 tickets 表。
3. 向数据库插入一张测试工单。
4. 创建真实 OllamaModel。
5. 把包含工单 ID 的问题交给 Agent Loop。
6. Ollama 决定调用 get_ticket_details。
7. Dispatcher 把请求转发给工单工具。
8. 工单工具通过 Repository 查询临时数据库。
9. 工具结果返回 Ollama。
10. Ollama 根据真实工单数据生成最终回答。
11. 打印回答并销毁临时数据库。

这个脚本：
- 会真实调用本机 Ollama。
- 不连接 Docker PostgreSQL。
- 不修改正式数据库。
- 不经过 FastAPI 和 JWT。
"""

# SQLAlchemy 用来创建临时数据库 Engine 和 Session
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# 导入 Agent 完整循环
from ticketpilot.agent.loop import run_agent

# 导入真实 Ollama 模型
from ticketpilot.agent.providers.ollama import (
    OllamaModel,
)

# 导入全部 ORM 模型共用的 Base
from ticketpilot.core.database import Base

# 导入工单 ORM 模型
from ticketpilot.tickets.models import (
    TicketRecord,
)

# 导入工单 Repository
from ticketpilot.tickets.repository import (
    create_ticket,
)


# 运行一次真实 Agent 冒烟测试
def main() -> None:
    # 创建只存在于当前进程内存中的 SQLite 数据库
    test_engine = create_engine(
        "sqlite:///:memory:",
    )

    # 根据已经登记的 ORM 模型创建数据库表
    Base.metadata.create_all(
        bind=test_engine,
    )

    try:
        # 创建本次测试独享的数据库 Session
        with Session(test_engine) as session:
            # 构造一张固定的测试工单
            ticket = TicketRecord(
                id="ticket-smoke-001",
                customer_name="冒烟测试客户",
                contact_email="smoke@example.com",
                channel="web",
                subject="无法登录 TicketPilot",
                description="输入正确密码后仍然无法进入系统",
                priority="high",
                status="in_progress",
                assigned_to="张三",
            )

            # 通过正式 Repository 保存测试工单
            create_ticket(
                session,
                ticket,
            )

            # 创建真正连接本地 Ollama 的模型
            model = OllamaModel()

            # 运行完整 Agent Loop
            answer = run_agent(
                session,
                model,
                (
                    "请查询工单 ticket-smoke-001，"
                    "告诉我它的标题、优先级、状态和处理人。"
                ),
            )

            # 打印最终自然语言回答
            print()
            print("Agent 最终回答：")
            print(answer)

    # 无论测试成功还是失败，都释放数据库资源
    finally:
        test_engine.dispose()


# 只有直接运行本文件时才执行冒烟测试
if __name__ == "__main__":
    main()