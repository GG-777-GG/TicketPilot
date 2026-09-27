"""
测试知识库 Repository 的事务职责。

本文件验证：
- add_document() 会把文档加入 Session。
- add_document() 会调用 flush() 提前执行 SQL。
- flush 成功后返回原来的文档对象。
- Repository 不会自行 commit。
- Repository 不会自行 rollback。
- 数据库异常会转换成 KnowledgeRepositoryError。

测试方式：
使用 MagicMock 模拟 SQLAlchemy Session，
不需要连接真实数据库，因此测试速度很快。
"""

# MagicMock 用来创建一个可检查调用记录的模拟对象。
from unittest.mock import MagicMock

# pytest 提供异常断言功能。
import pytest

# SQLAlchemyError 用来模拟底层数据库异常。
from sqlalchemy.exc import SQLAlchemyError

# Session 用作 MagicMock 的接口规范。
from sqlalchemy.orm import Session

# 导入知识文档 ORM 类型。
from ticketpilot.knowledge.models import (
    KnowledgeDocumentRecord,
)

# 导入准备测试的 Repository 函数和异常。
from ticketpilot.knowledge.repository import (
    KnowledgeRepositoryError,
    add_document,
)


# 验证 Repository 会加入并 flush 文档，但不会提交事务。
def test_add_document_adds_and_flushes_without_commit() -> None:
    # 创建一个模拟的 SQLAlchemy Session。
    #
    # spec=Session 表示这个模拟对象只能使用
    # 真正 Session 类型中存在的方法。
    session = MagicMock(
        spec=Session
    )

    # 创建一个模拟的知识文档 ORM 对象。
    document = MagicMock(
        spec=KnowledgeDocumentRecord
    )

    # 调用需要测试的 Repository 函数。
    saved_document = add_document(
        session,
        document,
    )

    # Repository 应该把文档加入 Session。
    session.add.assert_called_once_with(
        document
    )

    # Repository 应该执行一次 flush。
    session.flush.assert_called_once_with()

    # Repository 不能自行提交事务。
    session.commit.assert_not_called()

    # 正常情况下 Repository 也不能自行回滚。
    session.rollback.assert_not_called()

    # 函数应该返回调用者传入的同一个文档对象。
    assert saved_document is document


# 验证数据库异常会转换成知识库 Repository 异常。
def test_add_document_converts_database_error() -> None:
    # 创建模拟 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟文档。
    document = MagicMock(
        spec=KnowledgeDocumentRecord
    )

    # 指定调用 flush() 时抛出数据库异常。
    session.flush.side_effect = SQLAlchemyError(
        "模拟数据库写入失败"
    )

    # add_document() 应把 SQLAlchemyError
    # 转换成项目自己的 KnowledgeRepositoryError。
    with pytest.raises(
        KnowledgeRepositoryError,
        match="保存知识文档失败",
    ) as exception_info:
        add_document(
            session,
            document,
        )

    # 文档仍然应该先被加入 Session。
    session.add.assert_called_once_with(
        document
    )

    # flush 应该已经被调用过一次。
    session.flush.assert_called_once_with()

    # 即使失败，Repository 也不能自行提交。
    session.commit.assert_not_called()

    # rollback 由上层 Ingestion Service 负责。
    session.rollback.assert_not_called()

    # __cause__ 保存被转换之前的原始数据库异常。
    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )