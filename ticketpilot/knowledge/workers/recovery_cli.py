"""从命令行恢复租约过期的知识入库任务。

业务流程：
读取本次检查数量
→ 创建数据库 Session
→ 调用批量恢复 Service
→ 输出恢复数量并关闭 Session
"""

import argparse
from datetime import datetime, timezone

# 独立运行命令时，登记 users ORM 模型。
import ticketpilot.identity.models  # noqa: F401

from ticketpilot.core.database import SessionLocal
from ticketpilot.knowledge.application.jobs import recover_expired_ingestion_jobs


# 这个函数负责解析命令行参数并执行一次批量恢复。
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="恢复租约过期的知识入库任务"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="本次最多检查多少条过期任务，默认 100",
    )
    args = parser.parse_args(argv)

    if args.limit < 1:
        parser.error("--limit 必须大于 0")

    with SessionLocal() as session:
        recovered_ids = recover_expired_ingestion_jobs(
            session=session,
            now=datetime.now(timezone.utc),
            limit=args.limit,
        )

    print(f"本次恢复 {len(recovered_ids)} 条过期任务")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())