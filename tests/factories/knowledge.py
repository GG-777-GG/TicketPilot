"""创建知识库测试对象的工厂函数。"""

from hashlib import sha256
from uuid import uuid4

from ticketpilot.knowledge.chunking import CHUNKER_VERSION
from ticketpilot.knowledge.cleaning import CLEANER_VERSION
from ticketpilot.knowledge.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)


def _content_hash(content: str) -> str:
    """为测试内容生成与生产代码格式一致的 SHA-256。"""

    return sha256(
        content.encode("utf-8")
    ).hexdigest()


def build_knowledge_document(
    *,
    created_by: str,
    document_id: str | None = None,
    title: str = "密码重置说明",
    raw_content: str = "用户可以通过邮箱重置密码。",
    cleaned_content: str | None = None,
    content_hash: str | None = None,
    embedding_model: str = "qwen3-embedding:0.6b",
) -> KnowledgeDocumentRecord:
    """构造知识文档 ORM 对象，但不执行任何数据库操作。"""

    normalized_content = (
        cleaned_content
        if cleaned_content is not None
        else raw_content
    )

    return KnowledgeDocumentRecord(
        id=(
            document_id
            if document_id is not None
            else str(uuid4())
        ),
        title=title,
        raw_content=raw_content,
        cleaned_content=normalized_content,
        content_hash=(
            content_hash
            if content_hash is not None
            else _content_hash(normalized_content)
        ),
        embedding_model=embedding_model,
        cleaner_version=CLEANER_VERSION,
        chunker_version=CHUNKER_VERSION,
        created_by=created_by,
    )


def build_knowledge_chunk(
    *,
    chunk_index: int = 0,
    content: str = "用户可以通过邮箱重置密码。",
    chunk_id: str | None = None,
    content_hash: str | None = None,
    start_offset: int = 0,
    end_offset: int | None = None,
    embedding: list[float] | None = None,
) -> KnowledgeChunkRecord:
    """构造知识片段 ORM 对象，但不关联文档或写数据库。"""

    return KnowledgeChunkRecord(
        id=(
            chunk_id
            if chunk_id is not None
            else str(uuid4())
        ),
        chunk_index=chunk_index,
        content=content,
        content_hash=(
            content_hash
            if content_hash is not None
            else _content_hash(content)
        ),
        start_offset=start_offset,
        end_offset=(
            end_offset
            if end_offset is not None
            else start_offset + len(content)
        ),
        embedding=(
            embedding
            if embedding is not None
            else [0.0] * EMBEDDING_DIMENSIONS
        ),
    )
