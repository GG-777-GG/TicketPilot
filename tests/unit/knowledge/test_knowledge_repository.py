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
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)

# 导入准备测试的 Repository 函数和异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    add_document,
    add_document_version,
    get_document_version_by_number,
    get_document_record_for_update,
    list_document_versions,
    replace_document_chunks,
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


# 验证 Repository 会加入并 flush 历史版本，但不会提交事务。
def test_add_document_version_flushes_without_commit() -> None:
    session = MagicMock(
        spec=Session
    )
    document_version = MagicMock(
        spec=KnowledgeDocumentVersionRecord
    )

    saved_version = add_document_version(
        session,
        document_version,
    )

    session.add.assert_called_once_with(
        document_version
    )
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    assert saved_version is document_version


# 验证保存历史版本失败时会转换数据库异常。
def test_add_document_version_converts_database_error() -> None:
    session = MagicMock(
        spec=Session
    )
    document_version = MagicMock(
        spec=KnowledgeDocumentVersionRecord
    )
    session.flush.side_effect = SQLAlchemyError(
        "模拟历史版本写入失败"
    )

    with pytest.raises(
        KnowledgeRepositoryError,
        match="保存知识文档历史版本失败",
    ) as exception_info:
        add_document_version(
            session,
            document_version,
        )

    session.add.assert_called_once_with(
        document_version
    )
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )


# 验证替换片段会先删除旧片段，再保存新片段。
def test_replace_document_chunks_flushes_delete_before_insert() -> None:
    session = MagicMock(
        spec=Session
    )
    document = MagicMock(
        spec=KnowledgeDocumentRecord
    )
    old_chunk = MagicMock(
        spec=KnowledgeChunkRecord
    )
    first_new_chunk = MagicMock(
        spec=KnowledgeChunkRecord
    )
    second_new_chunk = MagicMock(
        spec=KnowledgeChunkRecord
    )
    document.chunks = [
        old_chunk,
    ]

    updated_document = replace_document_chunks(
        session,
        document,
        [
            first_new_chunk,
            second_new_chunk,
        ],
    )

    assert document.chunks == [
        first_new_chunk,
        second_new_chunk,
    ]
    assert session.flush.call_count == 2
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    assert updated_document is document


# 验证片段写入失败时会转换成 Repository 异常。
def test_replace_document_chunks_converts_database_error() -> None:
    session = MagicMock(
        spec=Session
    )
    document = MagicMock(
        spec=KnowledgeDocumentRecord
    )
    document.chunks = []
    new_chunk = MagicMock(
        spec=KnowledgeChunkRecord
    )

    # 第一次 flush 删除旧片段成功，第二次写入新片段失败。
    session.flush.side_effect = [
        None,
        SQLAlchemyError(
            "模拟新片段写入失败"
        ),
    ]

    with pytest.raises(
        KnowledgeRepositoryError,
        match="替换知识文档片段失败",
    ) as exception_info:
        replace_document_chunks(
            session,
            document,
            [
                new_chunk,
            ],
        )

    assert session.flush.call_count == 2
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )


# 验证更新查询会生成 FOR UPDATE 行锁语义。
def test_get_document_record_for_update_builds_locked_query() -> None:
    session = MagicMock(
        spec=Session
    )
    document = MagicMock(
        spec=KnowledgeDocumentRecord
    )
    session.scalar.return_value = document

    found_document = get_document_record_for_update(
        session,
        "document-001",
    )

    statement = session.scalar.call_args.args[0]

    # SQLAlchemy 在语句内部保存了 FOR UPDATE 配置。
    assert statement._for_update_arg is not None
    assert found_document is document
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 验证锁定查询失败时会转换成 Repository 异常。
def test_get_document_record_for_update_converts_database_error() -> None:
    session = MagicMock(
        spec=Session
    )
    session.scalar.side_effect = SQLAlchemyError(
        "模拟 FOR UPDATE 查询失败"
    )

    with pytest.raises(
        KnowledgeRepositoryError,
        match="锁定待更新知识文档失败",
    ) as exception_info:
        get_document_record_for_update(
            session,
            "document-001",
        )

    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 验证文档不存在时历史版本查询会返回 None。
def test_list_document_versions_returns_none_when_document_missing() -> None:
    session = MagicMock(
        spec=Session
    )
    session.scalar.return_value = None

    result = list_document_versions(
        session,
        "missing-document",
    )

    assert result is None
    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 验证历史版本查询失败时会转换成统一 Repository 异常。
def test_list_document_versions_converts_database_error() -> None:
    session = MagicMock(
        spec=Session
    )
    session.scalar.side_effect = SQLAlchemyError(
        "模拟历史版本查询失败"
    )

    with pytest.raises(
        KnowledgeRepositoryError,
        match="查询知识文档历史版本失败",
    ) as exception_info:
        list_document_versions(
            session,
            "document-001",
        )

    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 验证找不到指定历史版本时返回 None。
def test_get_document_version_by_number_returns_none_when_missing() -> None:
    session = MagicMock(
        spec=Session
    )
    session.execute.return_value.one_or_none.return_value = (
        None
    )

    result = get_document_version_by_number(
        session,
        "document-001",
        2,
    )

    assert result is None
    session.execute.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 验证历史版本详情查询失败时转换成统一异常。
def test_get_document_version_by_number_converts_database_error() -> None:
    session = MagicMock(
        spec=Session
    )
    session.execute.side_effect = SQLAlchemyError(
        "模拟历史版本详情查询失败"
    )

    with pytest.raises(
        KnowledgeRepositoryError,
        match="查询知识文档历史版本详情失败",
    ) as exception_info:
        get_document_version_by_number(
            session,
            "document-001",
            2,
        )

    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
