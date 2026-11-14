"""文件职责：提供知识入库 Worker 的命令行入口。

作用与配合：
持续模式启动时执行一次过期任务恢复，
再调用 Worker 领取并处理等待任务。

使用位置：
通过 python -m ticketpilot.knowledge.workers.worker_cli 启动。

边界：
启动恢复最多检查 100 条已经过期的任务；
本文件暂不负责定时恢复或心跳续租。
"""

import argparse

# Python 标准库：获取带 UTC 时区的当前时间。
from datetime import datetime, timezone

# Python 标准库的等待函数，参数单位是秒。
# 没领取到任务时等待一会儿，避免不停查询数据库。
from time import sleep

# 独立启动 Worker 时也要登记 users ORM 模型。
# 知识文档的 created_by 外键引用 users.id；缺少这次导入，
# SQLAlchemy 在写入文档时会报 NoReferencedTableError。
import ticketpilot.identity.models  # noqa: F401

from ticketpilot.core.database import SessionLocal
# 复用已有 Service，恢复过期运行任务或完成取消收尾。
from ticketpilot.knowledge.application.jobs import (
    recover_expired_ingestion_jobs,
)
from ticketpilot.knowledge.embeddings.ollama import OllamaEmbeddingProvider
from ticketpilot.knowledge.workers.worker import (
    process_ingestion_job,
    process_next_ingestion_job
)

# 批量处理流程：
# 创建一个向量 Provider
# → 每轮创建新的数据库 Session
# → 尝试处理下一条排队任务
# → 队列空了或达到数量上限时退出
def process_queued_jobs(*, max_jobs: int) -> int:
    """最多处理 max_jobs 条排队中的知识入库任务。"""
    if max_jobs < 1:
        raise ValueError("max_jobs 必须大于 0")

    provider = OllamaEmbeddingProvider()
    processed_count = 0

    for _ in range(max_jobs):
        # 每条任务使用独立的 Session。
        with SessionLocal() as session:
            result = process_next_ingestion_job(
                session=session,
                embedding_provider=provider,
            )

        if result is None:
            break

        print(f"任务 {result.id}: {result.status}")
        processed_count += 1

    return processed_count

def watch_queued_jobs(
    *,
    # 本轮没有领取到任务时，等待多少秒再尝试。
    poll_interval_seconds: int = 3,
) -> None:
    """持续尝试领取并处理排队中的知识入库任务。

    正常流程：
        检查等待间隔
        → 创建一个可复用的向量 Provider
        → 每轮创建独立 Session，尝试处理下一条任务
        → 处理完成后继续领取；未领取到任务时等待。

    异常处理：
        等待间隔不是正数时抛出 ValueError。
        单个任务正常返回 failed 时，继续处理后续任务。
        未被处理的系统异常继续向上传递，使进程退出。
        用户按 Ctrl+C 时，输出停止提示并结束循环。

    返回：
        无返回值；正常情况下持续运行，直到收到退出操作。

    调用约定：
        等待期间不持有数据库 Session。
        中断正在处理的任务时，可能留下 running 状态，
        后续需要使用已有的租约恢复机制处理。
    """

    # 必须有等待间隔，避免空队列时持续占用 CPU 和数据库。
    if poll_interval_seconds < 1:
        raise ValueError(
            "poll_interval_seconds 必须至少为 1 秒"
        )

    try:
        # 启动时先检查一批过期任务。
        # 使用独立 Session，恢复结束后关闭，再开始领取任务。
        with SessionLocal() as recovery_session:
            recovered_ids = recover_expired_ingestion_jobs(
                # 恢复操作使用的数据库会话。
                session=recovery_session,

                # 使用带 UTC 时区的当前时间判断租约是否过期。
                now=datetime.now(timezone.utc),

                # 本次启动最多检查 100 条过期任务。
                limit=100,
            )

        # Service 内部负责提交恢复结果。
        # 离开上面的 with 后，恢复 Session 已经关闭。
        # len() 是 Python 内置函数，取得实际恢复的任务数量。
        print(
            f"启动检查：恢复了 {len(recovered_ids)} 条过期任务。",
            flush=True,
        )

        # 恢复检查通过后，创建循环中复用的向量 Provider。
        provider = OllamaEmbeddingProvider()

        print(
            "Worker 已启动，持续检查等待任务；按 Ctrl+C 停止。",
            flush=True,
        )

        while True:
            # 每轮使用独立的数据库会话。
            # 离开 with 后关闭会话，再决定是否等待。
            with SessionLocal() as session:
                result = process_next_ingestion_job(
                    session=session,
                    embedding_provider=provider,
                )

                if result is not None:
                    # 任务失败也会返回记录，打印后继续检查下一条。
                    print(
                        f"任务 {result.id}: {result.status}",
                        flush=True,
                    )

            # None 表示本轮没有领取到任务。
            # 队列为空或任务刚被其他 Worker 抢先领取，都可能出现。
            if result is None:
                sleep(poll_interval_seconds)

    except KeyboardInterrupt:
        # Ctrl+C 通常会触发 Python 的 KeyboardInterrupt。
        # with 的退出清理仍会执行，数据库会话会被关闭。
        print(
            "\nWorker 已停止。",
            flush=True,
        )

# 这个函数负责解析命令行参数，选择单条或批量处理模式。
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="处理一条或多条知识入库任务"
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        help="本次最多处理多少条排队任务",
    )
    parser.add_argument("job_id", nargs="?", help="任务 ID；省略时自动处理下一条")

    parser.add_argument(
        # 启用持续运行模式。
        "--watch",

        # 出现 --watch 时保存为 True；不写时为 False。
        # 因此命令里只写 --watch，不需要再写 True。
        action="store_true",

        help="持续领取并处理排队任务，按 Ctrl+C 停止",
    )

    parser.add_argument(
        # 持续模式下，没有领取到任务时的等待秒数。
        "--poll-interval",

        # argparse 自动转换成整数；输入 abc 等内容会报错。
        type=int,

        # 不传时先记录为 None，后面采用默认的 3 秒。
        default=None,

        help="持续模式的空闲等待秒数，默认 3 秒，至少 1 秒",
    )

    args = parser.parse_args(argv)

    # 等待间隔只对持续模式有意义。
    if args.poll_interval is not None:
        if not args.watch:
            parser.error(
                "--poll-interval 必须与 --watch 一起使用"
            )

        if args.poll_interval < 1:
            parser.error(
                "--poll-interval 必须至少为 1 秒"
            )

    # 持续模式与指定任务、有限批量模式不能同时使用。
    if args.watch:
        if args.job_id is not None or args.max_jobs is not None:
            parser.error(
                "--watch 不能与任务 ID 或 --max-jobs 同时使用"
            )

        # 用户明确指定间隔时使用指定值，否则使用默认 3 秒。
        poll_interval_seconds = (
            args.poll_interval
            if args.poll_interval is not None
            else 3
        )

        watch_queued_jobs(
            poll_interval_seconds=poll_interval_seconds,
        )

        # 用户按 Ctrl+C 后，watch_queued_jobs() 会正常返回。
        # 随即结束 main()，不再进入下面的单条或批量分支。
        return 0

    # 批量模式：先检查参数，再处理排队任务。
    if args.max_jobs is not None:
        if args.job_id is not None:
            parser.error("任务 ID 不能与 --max-jobs 同时使用")

        if args.max_jobs < 1:
            parser.error("--max-jobs 必须大于 0")

        processed_count = process_queued_jobs(
            max_jobs=args.max_jobs
        )
        print(f"本次尝试处理 {processed_count} 条任务")
        return 0

    # with 结束时自动关闭数据库 Session。
    with SessionLocal() as session:
        provider = OllamaEmbeddingProvider()

        if args.job_id is None:
            # 没输入 ID：自动找下一条 queued 任务。
            result = process_next_ingestion_job(
                session=session,
                embedding_provider=provider,
            )
        else:
            # 输入了 ID：只处理指定任务。
            result = process_ingestion_job(
                session=session,
                job_id=args.job_id,
                embedding_provider=provider,
            )

    if result is None:
        if args.job_id is None:
            print("本次没有领取到待处理任务")
            return 0

        print("任务不存在，或当前状态不能领取")
        return 1

    print(f"任务 {args.job_id or result.id}: {result.status}")
    return 0 if result.status == "succeeded" else 2

if __name__ == "__main__":
    raise SystemExit(main())
