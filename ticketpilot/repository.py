# 导入 select，用来构造 SELECT 查询语句
from sqlalchemy import select

# 导入 SQLAlchemy 数据库异常的公共父类
from sqlalchemy.exc import SQLAlchemyError

# 导入 Session 类型，用来执行数据库操作
from sqlalchemy.orm import Session

# 导入数据库工单模型，对应 tickets 表
from ticketpilot.db_models import TicketRecord


# 创建并提交一张新工单
def create_ticket(
        # 接收调用者提供的数据库 Session
        session: Session,
        # 接收需要保存的 ORM 工单对象
        ticket: TicketRecord,
        # 声明成功时返回保存后的 ORM 工单对象
) -> TicketRecord:
    # 使用 try 捕获 SQLAlchemy 数据库异常
    try:
        # 将新对象加入当前 Session 的管理范围
        session.add(ticket)

        # 提交事务，SQLAlchemy 会执行 INSERT
        session.commit()

    # 捕获 SQLAlchemy 操作过程中产生的数据库异常
    except SQLAlchemyError as error:
        # 取消当前失败事务中尚未提交的变化
        session.rollback()

        # 转换成项目自己的 RuntimeError，隐藏数据库内部细节
        raise RuntimeError(
            "创建工单失败"
            # 保留原异常作为异常链，方便后台排查
        ) from error

    # 数据库提交成功后，将工单对象交还给调用者
    return ticket


# 查询数据库中的全部工单
def list_tickets(
        # 接收本次查询使用的数据库 Session
        session: Session,
        # 声明返回由 TicketRecord 组成的列表
) -> list[TicketRecord]:
    # 使用 try 捕获查询过程中可能出现的数据库异常
    try:
        # 构造查询 TicketRecord 的 SELECT 语句
        statement = select(
            TicketRecord
            # 按工单 ID 排序，使返回结果保持稳定
        ).order_by(
            TicketRecord.id
        )

        # 执行查询，并取得由 ORM 对象组成的结果
        result = session.scalars(statement)

        # 将查询结果转换成普通 Python 列表并返回
        return list(result.all())

    # 捕获数据库连接、SQL 执行等 SQLAlchemy 异常
    except SQLAlchemyError as error:
        # 让当前 Session 离开失败的事务状态
        session.rollback()

        # 转换为业务代码更容易理解的 RuntimeError
        raise RuntimeError(
            "查询工单列表失败"
            # 保留原始数据库异常用于排查
        ) from error


# 根据主键 ID 查询一张工单
def get_ticket_by_id(
        # 接收本次查询使用的数据库 Session
        session: Session,
        # 接收需要查询的工单主键
        ticket_id: str,
        # 找到时返回 TicketRecord，找不到时返回 None
) -> TicketRecord | None:
    # 使用 try 捕获查询过程中的数据库异常
    try:
        # Session.get() 专门用于根据主键查询
        return session.get(
            # 指定要查询的 ORM 模型，也就是 tickets 表
            TicketRecord,
            # 指定主键 id 的目标值
            ticket_id,
        )

    # 捕获所有 SQLAlchemy 数据库操作异常
    except SQLAlchemyError as error:
        # 清理当前失败的数据库事务
        session.rollback()

        # 向上层提供稳定的项目异常
        raise RuntimeError(
            "查询工单失败"
            # 保存异常链，后台仍能看到真正原因
        ) from error


# 修改一张已经存在的数据库工单状态
def update_ticket_status(
        # 接收正在管理这张工单的 Session
        session: Session,
        # 接收需要修改的 ORM 工单对象
        ticket: TicketRecord,
        # 接收准备写入的新状态
        new_status: str,
        # 修改成功后返回更新后的 ORM 工单对象
) -> TicketRecord:
    # 捕获修改和提交过程中可能发生的数据库错误
    try:
        # 修改被 Session 跟踪的对象属性
        ticket.status = new_status

        # 提交事务；SQLAlchemy 会生成 UPDATE SQL
        session.commit()

    # 捕获 SQLAlchemy 数据库异常
    except SQLAlchemyError as error:
        # 取消当前事务，使数据库恢复到提交前
        session.rollback()

        # 转换成项目统一的运行时异常
        raise RuntimeError(
            "修改工单状态失败"
            # 保留原始数据库异常，方便排查
        ) from error

    # 提交成功后返回修改过的工单对象
    return ticket

# 修改一张工单的当前处理人
def update_ticket_assignee(
        # 接收正在管理工单对象的数据库 Session
        session: Session,
        # 接收需要修改的 ORM 工单
        ticket: TicketRecord,
        # 字符串表示分配处理人，None 表示取消分配
        new_assignee: str | None,
        # 修改成功后返回更新后的 ORM 工单
) -> TicketRecord:
    # 捕获属性修改和事务提交过程中产生的数据库错误
    try:
        # 修改被当前 Session 跟踪的 ORM 对象
        ticket.assigned_to = new_assignee

        # 提交事务，SQLAlchemy 会执行 UPDATE
        session.commit()

    # 捕获 SQLAlchemy 数据库异常
    except SQLAlchemyError as error:
        # 撤销尚未成功提交的修改
        session.rollback()

        # 转换成项目统一使用的 RuntimeError
        raise RuntimeError(
            "修改工单处理人失败"
        ) from error

    # 提交成功后返回修改过的工单
    return ticket

# 删除一张已经存在的数据库工单
def delete_ticket(
        # 接收管理数据库事务的 Session
        session: Session,
        # 接收需要删除的 ORM 工单对象
        ticket: TicketRecord,
        # 删除成功不需要返回数据
) -> None:
    # 捕获删除和提交时可能发生的数据库错误
    try:
        # 将该对象标记为等待删除
        session.delete(ticket)

        # 提交事务；SQLAlchemy 会生成 DELETE SQL
        session.commit()

    # 捕获 SQLAlchemy 数据库异常
    except SQLAlchemyError as error:
        # 取消当前删除事务
        session.rollback()

        # 转换为项目统一的运行时异常
        raise RuntimeError(
            "删除工单失败"
            # 保留原始异常作为异常链
        ) from error
