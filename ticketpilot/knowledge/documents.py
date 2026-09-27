"""
定义 RAG 入库流水线内部使用的文档对象。

文件职责：
- 表示进入清洗器之前的原始文档。
- 表示经过清洗之后的标准化文档。
- 区分文档来源类型。
- 让清洗器、切块器和入库服务使用统一的数据结构。

第一阶段正常流程：
API 接收手动输入的标题和正文
→ 创建 RawDocument
→ 文档清洗器处理内容
→ 创建 CleanedDocument
→ 文本切块器处理 cleaned_content
→ 生成知识片段

异常流程：
- 空标题和空正文将在清洗器或 API 校验层被拒绝。
- 清洗后没有有效内容时，清洗器抛出业务异常。
- 本文件只负责携带数据，不负责数据库保存。

第二阶段重构：
支持 PDF、Word 和 HTML 后，将在 RawDocument 与
CleanedDocument 之间增加 ParsedDocument 和 DocumentBlock，
用于保存页码、章节、表格和原文位置。

完整调用链：
API Schema
→ RawDocument
→ DocumentCleaner
→ CleanedDocument
→ DocumentChunker
→ KnowledgeChunkRecord
→ PostgreSQL
"""

# dataclass 用来创建专门携带数据的内部对象。
from dataclasses import dataclass

# StrEnum 表示枚举值本身也是字符串。
from enum import StrEnum


# 定义知识文档可能具有的来源类型。
class KnowledgeSourceType(StrEnum):
    # 用户在 API 中直接输入文字。
    MANUAL = "manual"

    # 文档来自上传的本地文件。
    # 第一阶段暂不实现文件解析，但先保留类型。
    FILE = "file"

    # 文档来自网页地址。
    # 第一阶段暂不实现网页抓取，但先保留类型。
    URL = "url"

# 定义知识文档整体生命周期状态。
class KnowledgeDocumentStatus(StrEnum):
    # 文档已经接收，但尚未开始处理。
    PENDING = "pending"

    # 文档正在执行解析、清洗、切块或向量生成。
    PROCESSING = "processing"

    # 文档和全部片段已经准备完成，可以参与检索。
    READY = "ready"

    # 文档处理失败，需要检查错误或重新执行。
    FAILED = "failed"

    # 文档已经归档，不再参与正常检索。
    ARCHIVED = "archived"


# 定义文档处理过程当前执行到的具体阶段。
class KnowledgeProcessingStage(StrEnum):
    # 正在把 PDF、Word、HTML 等来源解析成统一内容。
    # 第一阶段手动文本不会实际使用，但为后续保留。
    PARSING = "parsing"

    # 正在规范化和清洗文档内容。
    CLEANING = "cleaning"

    # 正在把清洗后的内容拆成知识片段。
    CHUNKING = "chunking"

    # 正在为知识片段生成向量。
    EMBEDDING = "embedding"

    # 正在为向量和文本建立检索索引。
    INDEXING = "indexing"

# dataclass 自动生成 __init__() 等基础方法。
# frozen=True 表示对象创建后不能随意修改字段。
# slots=True 减少对象占用，并防止意外增加不存在的字段。
@dataclass(
    frozen=True,
    slots=True,
)
class RawDocument:
    """表示尚未经过清洗的原始文档。"""

    # 文档标题。
    title: str

    # 文档的原始正文。
    # 这里必须保留原文，不能被清洗结果覆盖。
    content: str

    # 文档来源类型。
    # 第一阶段默认由用户手动输入。
    source_type: KnowledgeSourceType = (
        KnowledgeSourceType.MANUAL
    )

    # 原始来源地址。
    # 手动输入时通常为 None；
    # 文件来源可以保存文件名，网页来源可以保存 URL。
    source_uri: str | None = None


# 表示经过清洗器处理后的文档。
@dataclass(
    frozen=True,
    slots=True,
)
class CleanedDocument:
    """表示完成基础清洗和指纹计算的文档。"""

    # 经过基础规范化处理的标题。
    title: str

    # 保留清洗前的原始正文。
    # 后续升级清洗算法时，可以重新处理。
    raw_content: str

    # 清洗后用于切块和检索的正文。
    cleaned_content: str

    # 文档的来源类型。
    source_type: KnowledgeSourceType

    # 文档原始来源地址。
    source_uri: str | None

    # 清洗后正文的 SHA-256 指纹。
    # 用来检测重复文档和内容变化。
    content_hash: str

    # 生成当前 cleaned_content 的清洗器版本。
    # 清洗规则升级后，可以识别哪些文档需要重新处理。
    cleaner_version: str