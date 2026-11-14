"""保存知识入库任务。

业务流程：
Service 构造任务对象
→ Repository 把任务写入当前事务
→ Service 决定最终 commit 或 rollback
"""
from dataclasses import dataclass
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from datetime import datetime

from ticketpilot.knowledge.persistence.models import (
    KnowledgeIngestionJobRecord,
    KnowledgeDocumentRecord,
)

from sqlalchemy import func, select, update
from ticketpilot.knowledge.domain.documents import (
    # 任务整体状态，例如 running。
    KnowledgeIngestionJobStatus,

    # 当前执行阶段，例如 embedding。
    KnowledgeProcessingStage,
)

class KnowledgeJobRepositoryError(RuntimeError):
    """表示入库任务的数据库操作失败。"""

# 表示一页入库任务查询结果。
# 这是普通 Python 数据类，不是数据库表，不需要数据库迁移。
@dataclass(frozen=True)
class KnowledgeIngestionJobListPage:
    """保存当前页的入库任务，以及符合查询条件的任务总数。

    用途：
        Repository 查询后，用这个对象把结果交给上层。

    字段：
        items：当前这一页的任务 ORM 对象。
        total：符合权限和筛选条件的全部任务数量，
               不只是当前页的数量。
    """

    # 当前页的任务集合。
    # tuple 中的 ... 表示可以包含任意数量的同类型元素。
    items: tuple[KnowledgeIngestionJobRecord, ...]

    # 符合查询条件的任务总数，用来计算分页。
    total: int

def list_ingestion_jobs(
    # 当前请求使用的数据库会话。
    session: Session,
    *,
    # 限定查询哪个用户提交的任务。
    # None 表示不限制提交人；上层只能为管理员传入 None。
    # 不设置默认值，要求每个调用者明确决定查询范围。
    requested_by: str | None,

    # 只查询某一种任务状态。
    # None 表示查询所有状态，保持原有调用行为。
    job_status: KnowledgeIngestionJobStatus | None = None,

    # 当前页最多返回多少条任务。
    limit: int = 20,

    # 跳过前面多少条任务。
    offset: int = 0,
) -> KnowledgeIngestionJobListPage:
    """分页查询指定范围内的入库任务。

       正常流程：
        校验分页参数 → 设置提交人和状态条件
        → 统计符合条件的总数 → 查询当前页 → 返回分页结果。

    异常：
        分页参数不合法时抛出 ValueError。
        数据库操作失败时抛出 KnowledgeJobRepositoryError。

    返回：
        当前页的任务 ORM 对象，以及符合条件的任务总数。
        没有匹配任务时，items 为空元组，total 为 0。

    调用约定：
        上层必须根据当前登录用户决定 requested_by。
        本函数不判断用户角色，也不提交事务。
    """
    # 防止无效或过大的单页查询。
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    if offset < 0:
        raise ValueError("offset 不能小于 0")

    # 查询任务对象，用于获得当前页的数据。
    statement = select(KnowledgeIngestionJobRecord)

    # 单独统计符合条件的任务总数，不受分页限制。
    total_statement = select(
        func.count(KnowledgeIngestionJobRecord.id)
    )

    # 两条查询必须使用相同条件：
    # 既不能返回其他人的任务，也不能泄露其他人的任务数量。
    if requested_by is not None:
        owner_condition = (
            KnowledgeIngestionJobRecord.requested_by
            == requested_by
        )
        statement = statement.where(owner_condition)
        total_statement = total_statement.where(
            owner_condition
        )

    # 指定状态时，同时限制当前页查询和总数统计。
    if job_status is not None:
        status_condition = (
            KnowledgeIngestionJobRecord.status
            == job_status.value
        )

        # 当前页只返回符合指定状态的任务。
        statement = statement.where(status_condition)

        # 总数也只统计符合该状态的任务。
        total_statement = total_statement.where(
            status_condition
        )
    # 最新提交的任务排在前面。
    # 时间相同时按 ID 排序，避免顺序不确定。
    statement = (
        statement
        .order_by(
            KnowledgeIngestionJobRecord.created_at.desc(),
            KnowledgeIngestionJobRecord.id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )

    try:
        # scalar() 取出 COUNT 查询返回的单个数值。
        total = session.scalar(total_statement)

        # scalars() 取出每行中的任务 ORM 对象。
        # all() 把当前页的全部结果收集成序列。
        rows = session.scalars(statement).all()
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "查询入库任务列表失败"
        ) from error

    return KnowledgeIngestionJobListPage(
        # tuple() 是 Python 内置类型，可把序列转成元组。
        items=tuple(rows),

        # COUNT 正常返回整数；这里兼容意外的 None。
        total=int(total or 0),
    )

# 这个函数负责把入库任务加入当前数据库事务。
def add_ingestion_job(
    session: Session,
    job: KnowledgeIngestionJobRecord,
) -> KnowledgeIngestionJobRecord:
    try:
        session.add(job)
        session.flush()
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "保存知识入库任务失败"
        ) from error

    return job

# 这个函数负责查找最早提交、仍在等待处理的任务 ID。
def find_next_queued_ingestion_job_id(
    session: Session,
) -> str | None:
    statement = (
        select(KnowledgeIngestionJobRecord.id)
        .where(
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.QUEUED.value
        )
        .order_by(
            KnowledgeIngestionJobRecord.created_at.asc(),
            KnowledgeIngestionJobRecord.id.asc(),
        )
        .limit(1)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "查询待处理入库任务失败"
        ) from error


# 查询流程：
# 接收一个截止时间
# → 只找 running 且 updated_at 早于截止时间的任务
# → 按时间排序并限制数量
# → 返回任务 ID，不修改数据库
def find_long_running_ingestion_job_ids(
    session: Session,
    *,
    cutoff: datetime,
    limit: int = 100,
) -> tuple[str, ...]:
    """查找运行时间较长、需要进一步检查的任务 ID。"""
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    statement = (
        select(KnowledgeIngestionJobRecord.id)
        .where(
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,
            KnowledgeIngestionJobRecord.updated_at < cutoff,
        )
        .order_by(
            KnowledgeIngestionJobRecord.updated_at.asc(),
            KnowledgeIngestionJobRecord.id.asc(),
        )
        .limit(limit)
    )

    try:
        return tuple(session.scalars(statement).all())
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "查询长时间运行的入库任务失败"
        ) from error


# 这个函数负责原子地领取一条等待处理的任务。
def claim_queued_ingestion_job(
        session: Session,
        job_id: str,
        *,
        lease_token: str,
        lease_expires_at: datetime,
) -> KnowledgeIngestionJobRecord | None:
    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.QUEUED.value,
        )
        .values(
            status=KnowledgeIngestionJobStatus.RUNNING.value,
            attempt_count=KnowledgeIngestionJobRecord.attempt_count + 1,
            last_error=None,
            updated_at=func.now(),
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            # 开始新一轮处理或处理结束时，清除旧阶段。
            processing_stage=None,
        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "领取知识入库任务失败"
        ) from error

# 这个函数负责把运行中的入库任务标记为成功，并关联知识文档。
def mark_ingestion_job_succeeded(
    session: Session,
    job_id: str,
    document_id: str,
    *,
    lease_token: str,
) -> KnowledgeIngestionJobRecord | None:
    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,
            KnowledgeIngestionJobRecord.lease_token == lease_token
        )
        .values(
            status=KnowledgeIngestionJobStatus.SUCCEEDED.value,
            document_id=document_id,
            last_error=None,
            updated_at=func.now(),
            lease_token=None,
            lease_expires_at=None,
            # 开始新一轮处理或处理结束时，清除旧阶段。
            processing_stage=None,
        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "完成知识入库任务失败"
        ) from error

# 这个函数负责记录运行中任务的失败原因。
def mark_ingestion_job_failed(
    session: Session,
    job_id: str,
    error_message: str,
    *,
    lease_token: str,
) -> KnowledgeIngestionJobRecord | None:
    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,
            KnowledgeIngestionJobRecord.lease_token == lease_token
        )
        .values(
            status=KnowledgeIngestionJobStatus.FAILED.value,
            last_error=error_message,
            updated_at=func.now(),
            lease_token=None,
            lease_expires_at=None,

        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "记录知识入库任务失败状态时出错"
        ) from error

# 这个函数负责把失败任务重新放回等待队列。
def requeue_failed_ingestion_job(
    session: Session,
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.FAILED.value,
        )
        .values(
            status=KnowledgeIngestionJobStatus.QUEUED.value,
            updated_at=func.now(),
            # 开始新一轮处理或处理结束时，清除旧阶段。
            processing_stage=None,
        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "重新排队知识入库任务失败"
        ) from error

# 这个函数负责按任务 ID 查询已保存的入库任务。
def get_ingestion_job(
    # 当前数据库会话。
    session: Session,

    # 要查询的任务 ID。
    job_id: str,

    *,
    # True 时重新读取数据库，覆盖 Session 缓存中的旧状态。
    fresh: bool = False,
) -> KnowledgeIngestionJobRecord | None:
    """查询任务，必要时强制刷新 ORM 缓存。"""

    try:
        if fresh:
            return session.get(
                KnowledgeIngestionJobRecord,
                job_id,
                populate_existing=True,
            )

        return session.get(
            KnowledgeIngestionJobRecord,
            job_id,
        )
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "查询知识入库任务失败"
        ) from error


def find_expired_ingestion_job_ids(
        session: Session, *, now: datetime, limit: int = 100
) -> tuple[str, ...]:
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    statement = (
        select(KnowledgeIngestionJobRecord.id)
        .where(
            # SQL IN：同时检查运行中和正在取消的过期任务。
            KnowledgeIngestionJobRecord.status.in_((
                KnowledgeIngestionJobStatus.RUNNING.value,
                KnowledgeIngestionJobStatus.CANCELLING.value,
            )),
            KnowledgeIngestionJobRecord.lease_expires_at.is_not(None),
            KnowledgeIngestionJobRecord.lease_expires_at <= now,
        )
        .order_by(
            KnowledgeIngestionJobRecord.lease_expires_at.asc(),
            KnowledgeIngestionJobRecord.id.asc(),
        )
        .limit(limit)
    )

    try:
        return tuple(session.scalars(statement).all())

    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "查询租约过期的入库任务失败"
        ) from error

# 这个类保存一条过期任务的巡检结果，不是数据库表。
@dataclass(frozen=True, slots=True)
class ExpiredIngestionJobInspection:
    # 正在处理的任务 ID。
    job_id: str

    # 相同 ID 的文档；没有写入文档时为 None。
    document_id: str | None

    # 文档状态；文档不存在时为 None。
    document_status: str | None


# 巡检流程：
# 筛选租约到期的 running 任务
# → 左连接相同 ID 的知识文档
# → 按到期时间和任务 ID 排序
# → 转换为只读结果，不修改数据库
def inspect_expired_ingestion_jobs(
    session: Session,
    *,
    now: datetime,
    limit: int = 100,
) -> tuple[ExpiredIngestionJobInspection, ...]:
    """查看过期任务是否已有对应文档，不执行恢复操作。"""
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    statement = (
        select(
            KnowledgeIngestionJobRecord.id.label("job_id"),
            KnowledgeDocumentRecord.id.label("document_id"),
            KnowledgeDocumentRecord.status.label("document_status"),
        )
        .select_from(KnowledgeIngestionJobRecord)
        .outerjoin(
            KnowledgeDocumentRecord,
            KnowledgeDocumentRecord.id == KnowledgeIngestionJobRecord.id,
        )
        .where(
            # SQL IN：同时检查运行中和正在取消的过期任务。
            KnowledgeIngestionJobRecord.status.in_((
                KnowledgeIngestionJobStatus.RUNNING.value,
                KnowledgeIngestionJobStatus.CANCELLING.value,
            )),
            KnowledgeIngestionJobRecord.lease_expires_at.is_not(None),
            KnowledgeIngestionJobRecord.lease_expires_at <= now,
        )
        .order_by(
            KnowledgeIngestionJobRecord.lease_expires_at.asc(),
            KnowledgeIngestionJobRecord.id.asc(),
        )
        .limit(limit)
    )

    try:
        rows = session.execute(statement).all()
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "巡检租约过期的入库任务失败"
        ) from error

    return tuple(
        ExpiredIngestionJobInspection(
            job_id=row.job_id,
            document_id=row.document_id,
            document_status=row.document_status,
        )
        for row in rows
    )

# 恢复流程：
# 确认任务仍在运行、租约已到期、token 未变化
# → 确认没有同 ID 的知识文档
# → 用一条 UPDATE 原子地改回 queued
# → 由上层决定是否 commit
def requeue_expired_ingestion_job(
    session: Session,
    job_id: str,
    *,
    lease_token: str,
    now: datetime,
) -> KnowledgeIngestionJobRecord | None:
    """安全地重新排队一条租约过期且尚未生成文档的任务。"""

    # 子查询：是否已经存在与任务同 ID 的知识文档。
    document_exists = (
        select(KnowledgeDocumentRecord.id)
        .where(
            KnowledgeDocumentRecord.id
            == KnowledgeIngestionJobRecord.id
        )
        .correlate(KnowledgeIngestionJobRecord)
        .exists()
    )

    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,
            KnowledgeIngestionJobRecord.lease_token == lease_token,
            KnowledgeIngestionJobRecord.lease_expires_at.is_not(None),
            KnowledgeIngestionJobRecord.lease_expires_at <= now,
            ~document_exists,
        )
        .values(
            status=KnowledgeIngestionJobStatus.QUEUED.value,
            lease_token=None,
            lease_expires_at=None,
            updated_at=func.now(),
            # 开始新一轮处理或处理结束时，清除旧阶段。
            processing_stage=None,
        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "重新排队租约过期的知识入库任务失败"
        ) from error


def update_ingestion_job_stage(
    # 本次更新使用的数据库会话；提交由上层负责。
    session: Session,

    # 要更新的任务 ID。
    job_id: str,

    *,
    # Worker 即将进入的处理阶段。
    stage: KnowledgeProcessingStage,

    # 本次领取任务时获得的租约标识，不是用户 JWT。
    lease_token: str,
) -> bool:
    """仅允许仍持有当前任务租约的 Worker 更新阶段。

    正常流程：
        检查任务 ID、running 状态和租约 token
        → 更新阶段与更新时间
        → 返回是否匹配到任务。

    返回：
        True：当前事务中的阶段更新成功。
        False：任务不存在、已结束，或租约已经变化。

    调用约定：
        本函数不 commit、不 rollback，
        也不修改任务整体状态和处理次数。
        上层提交后，其他请求才能读取到新阶段。
    """

    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            # 只能修改指定任务。
            KnowledgeIngestionJobRecord.id == job_id,

            # 已成功、失败或仍在排队的任务，不能上报执行阶段。
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,

            # 防止旧 Worker 覆盖新 Worker 的进度。
            KnowledgeIngestionJobRecord.lease_token
            == lease_token,
        )
        .values(
            # 例如枚举 EMBEDDING 的 value 是 "embedding"。
            processing_stage=stage.value,

            # 使用数据库当前时间记录这次更新。
            updated_at=func.now(),
        )
        # 更新匹配时返回任务 ID，没有匹配时不返回记录。
        .returning(KnowledgeIngestionJobRecord.id)
    )

    try:
        # scalar() 取出返回的单个 ID。
        # 有 ID 表示更新成功，None 表示没有符合条件的任务。
        return session.scalar(statement) is not None
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "更新知识入库任务处理阶段失败"
        ) from error


def cancel_queued_ingestion_job(
    # 当前数据库会话；提交由 Service 负责。
    session: Session,

    # 要取消的任务 ID。
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    """原子取消一条尚未被 Worker 领取的任务。

    正常流程：
        检查 ID 和 queued 状态
        → 改为 cancelled，清空阶段和租约
        → 返回更新后的任务。

    返回：
        成功时返回任务。
        不存在或已不是 queued 时返回 None。

    调用约定：
        不提交事务，不增加处理次数。
        保留原始输入、文件和上次错误。
    """

    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,

            # 必须在数据库更新时检查，而不是只看前端旧状态。
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.QUEUED.value,
        )
        .values(
            status=KnowledgeIngestionJobStatus.CANCELLED.value,
            processing_stage=None,
            lease_token=None,
            lease_expires_at=None,
            updated_at=func.now(),
        )
        .returning(KnowledgeIngestionJobRecord)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "取消等待中的知识入库任务失败"
        ) from error


def request_running_ingestion_job_cancellation(
    # 当前数据库会话，由上层决定是否提交。
    session: Session,

    # 用户申请取消的任务 ID。
    job_id: str,
) -> KnowledgeIngestionJobRecord | None:
    """为正在运行的任务登记取消申请。

    正常流程：
        原子检查 running 状态
        → 改为 cancelling
        → 保留租约、当前阶段和处理次数。

    返回：
        已登记申请的任务。
        不再是 running 时返回 None。

    调用约定：
        不代表 Worker 已停止，本函数不提交事务。
        Worker 和恢复逻辑接好后，接口才能调用此方法。
    """

    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.RUNNING.value,
        )
        .values(
            status=KnowledgeIngestionJobStatus.CANCELLING.value,
            updated_at=func.now(),
        )
        .returning(KnowledgeIngestionJobRecord)

        # 强制使用数据库返回的新值刷新已有 ORM 对象。
        # 避免返回缓存中的旧阶段或旧处理次数。
        .execution_options(populate_existing=True)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "登记知识入库任务取消申请失败"
        ) from error


def mark_ingestion_job_cancelled(
    # 确认取消使用的会话，由上层提交。
    session: Session,

    # 要确认已经停止处理的任务 ID。
    job_id: str,

    *,
    # Worker 本次领取的 token，防止旧 Worker 改动新任务。
    lease_token: str,

    # 恢复流程传入截止时间，要求租约已经到期。
    # 正常 Worker 确认自己已停止时不传。
    expired_before: datetime | None = None,
) -> KnowledgeIngestionJobRecord | None:
    """安全地将正在取消的任务收尾为已取消。

    正常流程：
        检查任务 ID、cancelling 状态和当前租约
        → 确认没有同 ID 的已保存文档
        → 按需检查租约已到期
        → 改为 cancelled，清空阶段和租约。

    返回：
        已取消的任务。
        条件不满足时返回 None。

    调用约定：
        Worker 必须先回滚文档写入事务，再确认取消。
        本函数不提交、不删除文档，也不增加处理次数。
    """

    # exists() 构造“是否存在对应文档”的 SQL 条件。
    document_exists = (
        select(KnowledgeDocumentRecord.id)
        .where(
            KnowledgeDocumentRecord.id
            == KnowledgeIngestionJobRecord.id
        )
        .correlate(KnowledgeIngestionJobRecord)
        .exists()
    )

    statement = (
        update(KnowledgeIngestionJobRecord)
        .where(
            KnowledgeIngestionJobRecord.id == job_id,

            # 只有已经申请取消的任务，才允许确认取消。
            KnowledgeIngestionJobRecord.status
            == KnowledgeIngestionJobStatus.CANCELLING.value,

            # 只能由持有当前租约的一方确认。
            KnowledgeIngestionJobRecord.lease_token
            == lease_token,

            # ~ 对 SQL 条件取反：
            # 不能已经存在同 ID 的保存结果。
            ~document_exists,
        )
    )

    if expired_before is not None:
        # 异常恢复时，额外证明租约已经到期。
        statement = statement.where(
            KnowledgeIngestionJobRecord.lease_expires_at.is_not(None),
            KnowledgeIngestionJobRecord.lease_expires_at
            <= expired_before,
        )

    statement = (
        statement
        .values(
            status=KnowledgeIngestionJobStatus.CANCELLED.value,
            processing_stage=None,
            lease_token=None,
            lease_expires_at=None,
            updated_at=func.now(),
        )
        .returning(KnowledgeIngestionJobRecord)
        .execution_options(populate_existing=True)
    )

    try:
        return session.scalar(statement)
    except SQLAlchemyError as error:
        raise KnowledgeJobRepositoryError(
            "确认知识入库任务取消失败"
        ) from error