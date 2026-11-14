"""
实现知识库模块的数据库访问层。

文件职责：
- 接收已经构造完成的知识文档 ORM 对象。
- 将文档及其全部知识片段加入 Session。
- 使用 flush() 提前执行 SQL 并发现数据库错误。
- 不在 Repository 内部提交事务。
- 把 SQLAlchemy 异常转换成知识库业务异常。

正常流程：
Ingestion Service
→ 创建 KnowledgeDocumentRecord
→ 把 KnowledgeChunkRecord 放入 document.chunks
→ add_document()
→ session.add(document)
→ ORM 级联加入全部 chunks
→ session.flush()
→ 返回 Ingestion Service
→ Service 执行 session.commit()

异常流程：
session.flush()
→ SQLAlchemyError
→ KnowledgeRepositoryError
→ 返回 Ingestion Service
→ Service 执行 session.rollback()

为什么 Repository 不执行 commit：
一次知识入库以后可能同时包含：
1. 保存文档。
2. 保存全部知识片段。
3. 更新入库任务。
4. 保存审计记录。

这些操作应该全部成功或者全部撤销，因此事务最终由
Ingestion Service 统一 commit 或 rollback。
"""

# SQLAlchemyError 是 SQLAlchemy 数据库异常的公共父类。
from sqlalchemy.exc import SQLAlchemyError

# Session 表示一次数据库操作会话和事务边界。
from sqlalchemy.orm import Session

# 只允许 ready 状态的文档参与检索。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)

# 导入知识文档 ORM 模型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)

# Sequence 表示有顺序、可以遍历的向量输入。
from collections.abc import Sequence

# dataclass 用来保存数据库查询返回的一行结果。
from dataclasses import dataclass

# func 提供 COUNT 等数据库函数，select 用来构造查询。
from sqlalchemy import (
    func,
    select,
)

# datetime 用于标注文档的创建时间和更新时间。
from datetime import datetime

# 表示知识库 Repository 执行数据库操作失败。
class KnowledgeRepositoryError(RuntimeError):
    """知识库数据库操作失败。"""

# 这个类保存知识文档列表查询返回的一项。
# 它只包含列表页面需要的字段，不加载正文和向量。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentSummaryRow:
    # 文档唯一 ID。
    id: str

    # 文档标题。
    title: str

    # manual、file 或 url。
    source_type: str

    # 文件地址或网页地址。
    source_uri: str | None

    # 文档当前状态。
    status: str

    # 文档的可见范围：internal 或 restricted。
    visibility: str

    # 当前文档包含多少个知识片段。
    chunk_count: int

    # 当前文档使用的向量模型。
    embedding_model: str

    # 向量维度。
    embedding_dimensions: int

    # 当前文档版本。
    version: int

    # 创建文档的用户 ID。
    created_by: str

    # 文档创建时间。
    created_at: datetime

    # 文档最后更新时间。
    updated_at: datetime

# 这个类保存单篇知识文档详情查询的结果。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentDetailRow:
    # 文档唯一 ID。
    id: str

    # 文档标题。
    title: str

    # 用户最初提交的原始正文。
    raw_content: str

    # 经过 Cleaning 层处理后的正文。
    cleaned_content: str

    # manual、file 或 url。
    source_type: str

    # 文件地址或者网页地址。
    source_uri: str | None

    # 文档当前状态。
    status: str

    # 文档的可见范围：internal 或 restricted。
    visibility: str

    # 当前正在执行的处理阶段。
    processing_stage: str | None

    # 当前文档包含多少个知识片段。
    chunk_count: int

    # 生成向量时使用的模型。
    embedding_model: str

    # 向量维度。
    embedding_dimensions: int

    # 清洗器版本。
    cleaner_version: str

    # 切块器版本。
    chunker_version: str

    # 当前文档版本。
    version: int

    # 创建文档的用户 ID。
    created_by: str

    # 文档创建时间。
    created_at: datetime

    # 文档最后更新时间。
    updated_at: datetime

# 这个类保存一次分页列表查询的完整结果。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentListPage:
    # 当前这一页的文档。
    items: tuple[
        KnowledgeDocumentSummaryRow,
        ...,
    ]

    # 数据库中全部知识文档的数量。
    total: int

# 把一篇文档及其全部知识片段加入当前数据库事务。
def add_document(
    # 接收当前请求或者当前业务流程使用的 Session。
    session: Session,

    # 接收已经构造完成的文档 ORM 对象。
    document: KnowledgeDocumentRecord,
) -> KnowledgeDocumentRecord:
    """把知识文档及其全部片段加入当前事务。"""

    # 捕获 SQLAlchemy 在写入数据库时产生的异常。
    try:
        # 把文档对象加入当前 Session。
        session.add(
            document
        )

        # KnowledgeDocumentRecord.chunks 配置了级联关系。
        # 因此加入 document 时，它包含的全部 chunk 也会被加入。
        #
        # flush() 会立即向数据库发送 INSERT，
        # 让外键、唯一约束和字段约束尽早接受检查。
        #
        # flush() 不等于 commit：
        # 此时数据仍在当前事务中，之后依然可以 rollback。
        session.flush()

    # 捕获连接失败、约束冲突等数据库异常。
    except SQLAlchemyError as error:
        # Repository 不在这里 rollback。
        # 因为事务的最终控制权属于 Ingestion Service。
        #
        # 使用 from error 保留真正的数据库异常链，
        # 方便后台日志排查具体原因。
        raise KnowledgeRepositoryError(
            "保存知识文档失败"
        ) from error

    # flush 成功后返回仍由当前 Session 管理的文档对象。
    return document

# 这个函数负责把一条知识文档历史版本加入当前事务。
def add_document_version(
    # 当前业务流程使用的数据库 Session。
    session: Session,

    # 已经由 Service 构造完成的历史版本 ORM 对象。
    document_version: (
        KnowledgeDocumentVersionRecord
    ),
) -> KnowledgeDocumentVersionRecord:
    """
    保存一条知识文档历史版本快照。

    业务流程：
    Update Service 构造历史版本对象
    → Repository 把对象加入 Session
    → flush 提前执行 INSERT
    → 返回 Update Service
    → Update Service 最后统一 commit
    """

    try:
        # 把历史版本 ORM 对象加入当前 Session。
        session.add(
            document_version
        )

        # 立即执行 INSERT，
        # 提前检查外键、版本唯一约束和字段约束。
        #
        # flush 不会最终提交事务，
        # 后续任何步骤失败时仍然可以 rollback。
        session.flush()

    # 捕获数据库连接、外键或唯一约束错误。
    except SQLAlchemyError as error:
        # Repository 不负责 rollback，
        # 整个更新事务由上层 Service 控制。
        raise KnowledgeRepositoryError(
            "保存知识文档历史版本失败"
        ) from error

    # 返回仍然由当前 Session 管理的历史版本对象。
    return document_version

# 这个函数负责使用新片段替换文档的全部旧片段。
def replace_document_chunks(
    # 当前更新业务使用的数据库 Session。
    session: Session,

    # 当前正在更新的知识文档 ORM 对象。
    document: KnowledgeDocumentRecord,

    # 清洗、切块和向量生成后得到的新片段。
    new_chunks: Sequence[
        KnowledgeChunkRecord
    ],
) -> KnowledgeDocumentRecord:
    """
    替换知识文档的全部片段。

    业务流程：
    删除当前文档的旧片段
    → flush 先执行 DELETE
    → 添加全部新片段
    → flush 执行 INSERT
    → 返回更新后的文档对象

    本函数不执行 commit 或 rollback。
    """

    try:
        # 清空 ORM 关系中的全部旧片段。
        #
        # chunks 配置了 delete-orphan，
        # 因此被移出关系的旧片段会被数据库删除。
        document.chunks.clear()

        # 先执行旧片段的 DELETE。
        #
        # 这里必须单独 flush，
        # 否则旧片段和新片段可能同时拥有相同的 chunk_index，
        # 从而触发 document_id + chunk_index 唯一约束。
        session.flush()

        # 把全部新片段关联到当前文档。
        #
        # relationship 会自动设置每个片段的 document_id。
        document.chunks.extend(
            new_chunks
        )

        # 执行新片段的 INSERT，
        # 提前检查索引、offset、外键和向量等约束。
        session.flush()

    # 捕获删除旧片段或保存新片段时发生的数据库错误。
    except SQLAlchemyError as error:
        # Repository 不控制最终事务，
        # rollback 由更新 Service 统一负责。
        raise KnowledgeRepositoryError(
            "替换知识文档片段失败"
        ) from error

    # 返回已经包含新片段的文档 ORM 对象。
    return document

# 这个函数负责根据 ID 查询单篇知识文档详情。
def get_document_by_id(
    # 当前请求使用的数据库 Session。
    session: Session,

    # 客户端希望查询的文档 ID。
    document_id: str,
) -> KnowledgeDocumentDetailRow | None:
    """查询单篇知识文档详情，找不到时返回 None。"""

    # 统计当前文档包含多少个知识片段。
    chunk_count_expression = (
        select(
            func.count(
                KnowledgeChunkRecord.id
            )
        )
        .where(
            KnowledgeChunkRecord.document_id
            == KnowledgeDocumentRecord.id
        )
        .correlate(
            KnowledgeDocumentRecord
        )
        .scalar_subquery()
    )

    # 只查询详情接口需要的字段。
    statement = (
        select(
            KnowledgeDocumentRecord.id,
            KnowledgeDocumentRecord.title,
            KnowledgeDocumentRecord.raw_content,
            KnowledgeDocumentRecord.cleaned_content,
            KnowledgeDocumentRecord.source_type,
            KnowledgeDocumentRecord.source_uri,
            KnowledgeDocumentRecord.status,
            # 从数据库读取文档可见范围。
            KnowledgeDocumentRecord.visibility,
            KnowledgeDocumentRecord.processing_stage,

            # 使用数据库子查询统计片段数量。
            chunk_count_expression.label(
                "chunk_count"
            ),

            KnowledgeDocumentRecord.embedding_model,
            KnowledgeDocumentRecord.embedding_dimensions,
            KnowledgeDocumentRecord.cleaner_version,
            KnowledgeDocumentRecord.chunker_version,
            KnowledgeDocumentRecord.version,
            KnowledgeDocumentRecord.created_by,
            KnowledgeDocumentRecord.created_at,
            KnowledgeDocumentRecord.updated_at,
        )
        .where(
            # 主键必须等于客户端提供的文档 ID。
            KnowledgeDocumentRecord.id
            == document_id
        )
    )

    try:
        # 主键最多只能匹配一条记录。
        row = session.execute(
            statement
        ).one_or_none()

    # 把数据库异常转换成项目统一异常。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "查询知识文档详情失败"
        ) from error

    # 数据库中没有这个 ID。
    if row is None:
        return None

    # 把 SQLAlchemy Row 转换成明确的只读详情对象。
    return KnowledgeDocumentDetailRow(
        id=row.id,
        title=row.title,
        raw_content=row.raw_content,
        cleaned_content=(
            row.cleaned_content
        ),
        source_type=row.source_type,
        source_uri=row.source_uri,
        status=row.status,
        visibility=row.visibility,
        processing_stage=(
            row.processing_stage
        ),
        chunk_count=int(
            row.chunk_count
        ),
        embedding_model=(
            row.embedding_model
        ),
        embedding_dimensions=(
            row.embedding_dimensions
        ),
        cleaner_version=(
            row.cleaner_version
        ),
        chunker_version=(
            row.chunker_version
        ),
        version=row.version,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

# 这个函数负责查询一篇可以被修改的知识文档 ORM 对象。
def get_document_record_by_id(
    # 当前业务流程使用的数据库 Session。
    session: Session,

    # 要查找的知识文档 ID。
    document_id: str,
) -> KnowledgeDocumentRecord | None:
    """
    根据 ID 查询知识文档 ORM 对象。

    业务流程：
    文档管理 Service
    → 调用本函数查询 ORM 对象
    → 修改文档状态
    → Service 提交事务
    """

    try:
        # session.get() 根据主键查询一条 ORM 记录。
        document = session.get(
            KnowledgeDocumentRecord,
            document_id,
        )

    # 数据库连接失败或者查询失败。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "查询知识文档记录失败"
        ) from error

    # 找不到时返回 None；
    # 后面的 Router 会把它转换成 HTTP 404。
    return document
# 这个函数负责查询并锁定即将更新的知识文档。
def get_document_record_for_update(
    # 当前更新业务使用的数据库 Session。
    session: Session,

    # 要更新的知识文档 ID。
    document_id: str,
) -> KnowledgeDocumentRecord | None:
    """
    查询并锁定一篇知识文档。

    PostgreSQL 会生成类似 SQL：

    SELECT *
    FROM knowledge_documents
    WHERE id = :document_id
    FOR UPDATE

    这把锁会保持到当前事务 commit 或 rollback。
    """

    # 构造根据主键查询文档的 SQL。
    statement = (
        select(
            KnowledgeDocumentRecord
        )
        .where(
            KnowledgeDocumentRecord.id
            == document_id
        )

        # 告诉数据库锁定查询到的这一行。
        .with_for_update()
    )

    try:
        # 主键最多只能查询到一篇文档。
        #
        # 找不到时 scalar() 返回 None。
        return session.scalar(
            statement
        )

    # 捕获数据库连接或查询错误。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "锁定待更新知识文档失败"
        ) from error

# 这个函数负责分页查询知识文档列表。
def list_documents(
    # 当前请求使用的数据库 Session。
    session: Session,

    # 星号后面的参数必须写出参数名。
    *,

    # 当前页面最多返回多少篇文档。
    limit: int = 20,

    # 跳过前面的多少篇文档。
    offset: int = 0,
) -> KnowledgeDocumentListPage:
    """分页查询知识文档摘要和文档总数。"""

    # 创建一个相关子查询，统计每篇文档拥有多少个片段。
    #
    # 它最后会生成类似：
    # SELECT COUNT(knowledge_chunks.id)
    # WHERE knowledge_chunks.document_id = knowledge_documents.id
    chunk_count_expression = (
        select(
            func.count(
                KnowledgeChunkRecord.id
            )
        )
        .where(
            KnowledgeChunkRecord.document_id
            == KnowledgeDocumentRecord.id
        )
        .correlate(
            KnowledgeDocumentRecord
        )
        .scalar_subquery()
    )

    # 构造当前页面的文档查询。
    statement = (
        select(
            # 只查询列表页面需要的字段。
            KnowledgeDocumentRecord.id,
            KnowledgeDocumentRecord.title,
            KnowledgeDocumentRecord.source_type,
            KnowledgeDocumentRecord.source_uri,
            KnowledgeDocumentRecord.status,
            # 一起从数据库读取文档可见范围。
            KnowledgeDocumentRecord.visibility,

            # 把片段数量命名为 chunk_count。
            chunk_count_expression.label(
                "chunk_count"
            ),

            KnowledgeDocumentRecord.embedding_model,
            KnowledgeDocumentRecord.embedding_dimensions,
            KnowledgeDocumentRecord.version,
            KnowledgeDocumentRecord.created_by,
            KnowledgeDocumentRecord.created_at,
            KnowledgeDocumentRecord.updated_at,
        )

        # 新创建的文档排在前面。
        .order_by(
            KnowledgeDocumentRecord.created_at.desc(),

            # 创建时间相同时使用 ID 保证顺序稳定。
            KnowledgeDocumentRecord.id.asc(),
        )

        # 跳过前面的 offset 条数据。
        .offset(
            offset
        )

        # 最多返回 limit 条数据。
        .limit(
            limit
        )
    )

    # 单独统计数据库中的文档总数。
    total_statement = select(
        func.count(
            KnowledgeDocumentRecord.id
        )
    )

    try:
        # 查询数据库中的文档总数。
        total = session.scalar(
            total_statement
        )

        # 查询当前页面需要的文档。
        rows = session.execute(
            statement
        ).all()

    # 把数据库异常转换成统一的 Repository 异常。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "查询知识文档列表失败"
        ) from error

    # 把 SQLAlchemy Row 转换成明确的只读结果对象。
    items = tuple(
        KnowledgeDocumentSummaryRow(
            id=row.id,
            title=row.title,
            source_type=row.source_type,
            source_uri=row.source_uri,
            status=row.status,
            visibility=row.visibility,
            chunk_count=int(
                row.chunk_count
            ),
            embedding_model=(
                row.embedding_model
            ),
            embedding_dimensions=(
                row.embedding_dimensions
            ),
            version=row.version,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    )

    # 同时返回当前页数据和全部文档数量。
    return KnowledgeDocumentListPage(
        items=items,

        # COUNT 正常情况下总会返回整数；
        # 这里继续防御异常的 None。
        total=int(
            total or 0
        ),
    )

# 这个类保存知识文档历史版本列表中的一项。
#
# 列表只返回摘要信息，
# 不返回 raw_content 和 cleaned_content，
# 避免一次查询加载大量历史正文。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentVersionSummaryRow:
    # 这个历史版本属于哪篇文档。
    document_id: str

    # 历史版本号，例如 1、2、3。
    version: int

    # 这个版本当时使用的标题。
    title: str

    # 这个版本当时的来源类型。
    source_type: str

    # 这个版本当时的文件地址或网页地址。
    source_uri: str | None

    # 这个版本当时使用的向量模型。
    embedding_model: str

    # 这个版本当时的向量维度。
    embedding_dimensions: int

    # 这个版本当时使用的清洗器版本。
    cleaner_version: str

    # 这个版本当时使用的切块器版本。
    chunker_version: str

    # 最初创建这个内容版本的用户 ID。
    created_by: str

    # 这个内容版本最初创建的时间。
    created_at: datetime

    # 后来由哪个用户替换了这个版本。
    superseded_by: str

    # 这个版本被替换的时间。
    superseded_at: datetime

# 这个类保存某一个历史版本的完整详情。
#
# 它继承摘要中的版本号、标题、来源、
# 模型、操作人和时间等公共字段，
# 再增加历史版本的完整正文。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentVersionDetailRow(
    KnowledgeDocumentVersionSummaryRow
):
    # 用户当时提交的原始正文。
    raw_content: str

    # 经过当时 Cleaning 层处理后的正文。
    cleaned_content: str


# 这个类保存历史版本分页查询的完整结果。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeDocumentVersionListPage:
    # 当前页面查询到的历史版本摘要。
    items: tuple[
        KnowledgeDocumentVersionSummaryRow,
        ...,
    ]

    # 这篇文档一共有多少条历史版本。
    total: int

    # 当前知识文档正在使用的版本号。
    current_version: int

# 这个函数负责分页查询一篇知识文档的历史版本。
def list_document_versions(
    # 当前请求使用的数据库 Session。
    session: Session,

    # 要查询哪一篇知识文档的历史版本。
    document_id: str,

    # 星号后面的参数必须写出参数名。
    *,

    # 当前页面最多返回多少条历史版本。
    limit: int = 20,

    # 跳过前面的多少条历史版本。
    offset: int = 0,
) -> KnowledgeDocumentVersionListPage | None:
    """
    分页查询知识文档的历史版本。

    返回 None：
    当前知识文档不存在。

    返回 items=()：
    文档存在，但是还没有产生历史版本。
    """

    # 查询当前文档的版本号。
    #
    # 这个查询还有一个作用：
    # 判断 knowledge_documents 中是否存在这篇文档。
    current_version_statement = (
        select(
            KnowledgeDocumentRecord.version
        )
        .where(
            KnowledgeDocumentRecord.id
            == document_id
        )
    )

    # 统计这篇文档拥有多少条历史版本。
    total_statement = (
        select(
            func.count(
                KnowledgeDocumentVersionRecord.id
            )
        )
        .where(
            KnowledgeDocumentVersionRecord.document_id
            == document_id
        )
    )

    # 查询当前页面需要的历史版本摘要。
    versions_statement = (
        select(
            KnowledgeDocumentVersionRecord.document_id,
            KnowledgeDocumentVersionRecord.version,
            KnowledgeDocumentVersionRecord.title,
            KnowledgeDocumentVersionRecord.source_type,
            KnowledgeDocumentVersionRecord.source_uri,
            KnowledgeDocumentVersionRecord.embedding_model,
            KnowledgeDocumentVersionRecord.embedding_dimensions,
            KnowledgeDocumentVersionRecord.cleaner_version,
            KnowledgeDocumentVersionRecord.chunker_version,
            KnowledgeDocumentVersionRecord.created_by,
            KnowledgeDocumentVersionRecord.created_at,
            KnowledgeDocumentVersionRecord.superseded_by,
            KnowledgeDocumentVersionRecord.superseded_at,
        )

        # 只查询指定文档的历史版本。
        .where(
            KnowledgeDocumentVersionRecord.document_id
            == document_id
        )

        # 新版本排在前面。
        #
        # 例如历史版本有 1、2、3，
        # 返回顺序就是 3、2、1。
        .order_by(
            KnowledgeDocumentVersionRecord.version.desc()
        )

        # 跳过前面的 offset 条记录。
        .offset(
            offset
        )

        # 最多返回 limit 条记录。
        .limit(
            limit
        )
    )

    try:
        # scalar() 适合读取单个值。
        #
        # 找不到文档时会得到 None。
        current_version = session.scalar(
            current_version_statement
        )

        # 当前文档不存在。
        if current_version is None:
            return None

        # 查询历史版本总数。
        total = session.scalar(
            total_statement
        )

        # 查询当前页面的历史版本。
        rows = session.execute(
            versions_statement
        ).all()

    # 把 SQLAlchemy 查询异常转换成统一异常。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "查询知识文档历史版本失败"
        ) from error

    # 把 SQLAlchemy Row 转换成明确的只读结果对象。
    items = tuple(
        KnowledgeDocumentVersionSummaryRow(
            document_id=row.document_id,
            version=row.version,
            title=row.title,
            source_type=row.source_type,
            source_uri=row.source_uri,
            embedding_model=(
                row.embedding_model
            ),
            embedding_dimensions=(
                row.embedding_dimensions
            ),
            cleaner_version=(
                row.cleaner_version
            ),
            chunker_version=(
                row.chunker_version
            ),
            created_by=row.created_by,
            created_at=row.created_at,
            superseded_by=(
                row.superseded_by
            ),
            superseded_at=(
                row.superseded_at
            ),
        )
        for row in rows
    )

    # 返回历史版本分页结果。
    return KnowledgeDocumentVersionListPage(
        items=items,

        # COUNT 正常情况下返回整数；
        # 使用 or 0 防御异常的 None。
        total=int(
            total or 0
        ),

        # 当前文档正在使用的版本号。
        current_version=current_version,
    )

# 这个函数负责根据文档 ID 和版本号查询历史版本详情。
def get_document_version_by_number(
    # 当前请求使用的数据库 Session。
    session: Session,

    # 历史版本属于哪一篇知识文档。
    document_id: str,

    # 要查询的历史版本号。
    version: int,
) -> KnowledgeDocumentVersionDetailRow | None:
    """
    查询一个历史版本的完整详情。

    找不到指定历史版本时返回 None。
    """

    # 只查询历史版本详情接口需要的字段。
    statement = (
        select(
            KnowledgeDocumentVersionRecord.document_id,
            KnowledgeDocumentVersionRecord.version,
            KnowledgeDocumentVersionRecord.title,
            KnowledgeDocumentVersionRecord.raw_content,
            KnowledgeDocumentVersionRecord.cleaned_content,
            KnowledgeDocumentVersionRecord.source_type,
            KnowledgeDocumentVersionRecord.source_uri,
            KnowledgeDocumentVersionRecord.embedding_model,
            KnowledgeDocumentVersionRecord.embedding_dimensions,
            KnowledgeDocumentVersionRecord.cleaner_version,
            KnowledgeDocumentVersionRecord.chunker_version,
            KnowledgeDocumentVersionRecord.created_by,
            KnowledgeDocumentVersionRecord.created_at,
            KnowledgeDocumentVersionRecord.superseded_by,
            KnowledgeDocumentVersionRecord.superseded_at,
        )

        # 必须属于客户端指定的知识文档。
        .where(
            KnowledgeDocumentVersionRecord.document_id
            == document_id
        )

        # 版本号也必须相同。
        .where(
            KnowledgeDocumentVersionRecord.version
            == version
        )
    )

    try:
        # document_id + version 存在唯一约束，
        # 所以最多只能查到一条记录。
        row = session.execute(
            statement
        ).one_or_none()

    # 把数据库异常转换成统一 Repository 异常。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "查询知识文档历史版本详情失败"
        ) from error

    # 没有找到对应历史版本。
    if row is None:
        return None

    # 把 SQLAlchemy Row 转换成明确的只读详情对象。
    return KnowledgeDocumentVersionDetailRow(
        document_id=row.document_id,
        version=row.version,
        title=row.title,
        source_type=row.source_type,
        source_uri=row.source_uri,
        embedding_model=(
            row.embedding_model
        ),
        embedding_dimensions=(
            row.embedding_dimensions
        ),
        cleaner_version=(
            row.cleaner_version
        ),
        chunker_version=(
            row.chunker_version
        ),
        created_by=row.created_by,
        created_at=row.created_at,
        superseded_by=(
            row.superseded_by
        ),
        superseded_at=(
            row.superseded_at
        ),
        raw_content=row.raw_content,
        cleaned_content=(
            row.cleaned_content
        ),
    )

# 这个类负责保存 Repository 查询到的一条知识片段记录。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeChunkSearchRow:
    # 知识片段 ID。
    chunk_id: str

    # 片段所属文档 ID。
    document_id: str

    # 所属文档标题。
    document_title: str

    # 当前文档版本。
    document_version: int

    # 数据库中保存的来源类型字符串。
    source_type: str

    # 文件名或网页地址。
    source_uri: str | None

    # 片段在文档中的顺序。
    chunk_index: int

    # 片段正文。
    content: str

    # 片段在清洗后正文中的位置。
    start_offset: int
    end_offset: int

    # 片段对应的 PDF 原始页码；没有页码时为空元组。
    page_numbers: tuple[int, ...]

    # PostgreSQL 计算出的余弦距离。
    cosine_distance: float

# 这个函数负责使用 pgvector 查找最相似的知识片段。
def search_ready_chunks_by_cosine(
    session: Session,
    *,
    # 用户问题生成的查询向量。
    query_embedding: Sequence[float],

    # 只搜索由同一个模型生成的文档向量。
    embedding_model: str,

    # 只搜索维度相同的文档向量。
    embedding_dimensions: int,

    # 最多返回多少个片段。
    top_k: int,

    # 最低允许的余弦相似度。
    min_similarity: float,

    # 本次允许检索的文档类型；未指定时只能查内部文档。
    allowed_visibilities: tuple[str, ...] = ("internal",),
) -> tuple[
    KnowledgeChunkSearchRow,
    ...,
]:
    """使用余弦距离查询 ready 状态的知识片段。"""

    # pgvector 需要普通 list 作为查询向量。
    query_vector = list(
        query_embedding
    )

    # cosine_distance() 会生成 pgvector 的 <=> 运算。
    #
    # 例如：
    # knowledge_chunks.embedding <=> '[...]'
    distance_expression = (
        KnowledgeChunkRecord
        .embedding
        .cosine_distance(
            query_vector
        )
    )

    # 只查询返回结果真正需要的字段。
    statement = (
        select(
            KnowledgeChunkRecord.id.label(
                "chunk_id"
            ),
            KnowledgeChunkRecord.document_id.label(
                "document_id"
            ),
            KnowledgeDocumentRecord.title.label(
                "document_title"
            ),
            KnowledgeDocumentRecord.version.label(
                "document_version"
            ),
            KnowledgeDocumentRecord.source_type.label(
                "source_type"
            ),
            KnowledgeDocumentRecord.source_uri.label(
                "source_uri"
            ),
            KnowledgeChunkRecord.chunk_index.label(
                "chunk_index"
            ),
            KnowledgeChunkRecord.content.label(
                "content"
            ),
            KnowledgeChunkRecord.start_offset.label(
                "start_offset"
            ),
            KnowledgeChunkRecord.end_offset.label(
                "end_offset"
            ),
            # 同时从数据库读取片段的来源页码。
            KnowledgeChunkRecord.page_numbers.label(
                "page_numbers"
            ),

            # 把数据库计算结果命名为 cosine_distance。
            distance_expression.label(
                "cosine_distance"
            ),
        )

        # 每个片段必须连接到所属文档。
        .join(
            KnowledgeDocumentRecord,
            (
                KnowledgeChunkRecord.document_id
                == KnowledgeDocumentRecord.id
            ),
        )

        # 过滤不能参与当前查询的数据。
        .where(
            # 只有处理完成的文档才能参与检索。
            KnowledgeDocumentRecord.status
            == KnowledgeDocumentStatus.READY.value,

            # 先在数据库中过滤权限，再排序和执行 LIMIT。
            KnowledgeDocumentRecord.visibility.in_(
                allowed_visibilities
            ),

            # 不同模型的向量不能混合比较。
            KnowledgeDocumentRecord.embedding_model
            == embedding_model,

            # 向量维度必须保持一致。
            KnowledgeDocumentRecord.embedding_dimensions
            == embedding_dimensions,

            # similarity = 1 - distance
            # 所以 distance 必须小于等于 1 - 最低相似度。
            distance_expression
            <= (
                1.0
                - min_similarity
            ),
        )

        # 距离越小越相似，因此使用升序排列。
        .order_by(
            distance_expression.asc(),

            # 相同距离时使用 ID 保证顺序稳定。
            KnowledgeChunkRecord.id.asc(),
        )

        # 只返回最前面的 top_k 项。
        .limit(
            top_k
        )
    )

    try:
        # 真正把 SELECT 发送给数据库。
        rows = session.execute(
            statement
        ).all()

    # 捕获连接失败或 SQL 执行错误。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "搜索知识片段失败"
        ) from error

    # 把 SQLAlchemy Row 转换成明确的数据对象。
    return tuple(
        KnowledgeChunkSearchRow(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            document_title=(
                row.document_title
            ),
            document_version=(
                row.document_version
            ),
            source_type=row.source_type,
            source_uri=row.source_uri,
            chunk_index=row.chunk_index,
            content=row.content,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            # 数据库 JSON 读出的是 list，查询结果对象使用 tuple。
            page_numbers=tuple(
                row.page_numbers
            ),
            cosine_distance=float(
                row.cosine_distance
            ),
        )
        for row in rows
    )