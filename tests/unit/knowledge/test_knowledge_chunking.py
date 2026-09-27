"""
测试知识库第一阶段的字符级切块器。

测试目标：
- 短文档只生成一个片段。
- 长文档能够生成多个片段。
- 每个片段不超过最大字符限制。
- 相邻片段保留指定长度的重叠。
- 切块器优先在自然句子边界截断。
- offset 能从原文准确还原片段。
- 每个片段拥有正确的 SHA-256 指纹。
- 非法切块参数会被拒绝。
- 空正文不会生成知识片段。

这些测试用于防止：
- 文本内容丢失
- offset 偏移一个字符
- 片段长度超过限制
- 重叠区域错误
- 循环无法前进
"""

# sha256 用来验证片段的内容指纹。
from hashlib import sha256

# pytest 提供异常断言和参数化测试。
import pytest

# 导入切块器、异常和默认切块函数。
from ticketpilot.knowledge.chunking import (
    DocumentChunkingError,
    chunk_document,
)

# 导入清洗后的文档对象和来源类型。
from ticketpilot.knowledge.documents import (
    CleanedDocument,
    KnowledgeSourceType,
)


# 创建测试专用的 CleanedDocument。
def build_cleaned_document(
    content: str,
) -> CleanedDocument:
    # 这里直接构造清洗结果，
    # 让切块测试不依赖 cleaning.py 的实现。
    return CleanedDocument(
        title="测试知识文档",
        raw_content=content,
        cleaned_content=content,
        source_type=(
            KnowledgeSourceType.MANUAL
        ),
        source_uri=None,
        content_hash="test-document-hash",
        cleaner_version="test-cleaner",
    )


# 验证短文档只生成一个片段。
def test_short_document_creates_one_chunk() -> None:
    # 准备一个小于最大字符限制的正文。
    content = "用户可以通过邮箱重置登录密码。"

    # 创建测试文档。
    document = build_cleaned_document(
        content
    )

    # 使用较小参数，让测试意图更加清晰。
    chunks = chunk_document(
        document,
        max_characters=50,
        overlap_characters=10,
    )

    # 短文档应该只产生一个片段。
    assert len(chunks) == 1

    # 取得唯一的片段。
    chunk = chunks[0]

    # 第一个片段编号必须从 0 开始。
    assert chunk.chunk_index == 0

    # 片段内容应该等于完整正文。
    assert chunk.content == content

    # 开始位置应该是正文开头。
    assert chunk.start_offset == 0

    # 结束位置应该等于正文长度。
    assert chunk.end_offset == len(
        content
    )

    # character_count 属性应返回实际字符数量。
    assert chunk.character_count == len(
        content
    )


# 验证长文档会生成多个具有重叠内容的片段。
def test_long_document_creates_overlapping_chunks() -> None:
    # 生成 120 个字符，并且不包含自然分隔符。
    # 这样可以准确验证固定长度和重叠行为。
    content = "".join(
        str(index % 10)
        for index in range(120)
    )

    # 创建测试文档。
    document = build_cleaned_document(
        content
    )

    # 每段最多 50 个字符，相邻段重叠 10 个字符。
    chunks = chunk_document(
        document,
        max_characters=50,
        overlap_characters=10,
    )

    # 预期范围：
    # Chunk 0：0～50
    # Chunk 1：40～90
    # Chunk 2：80～120
    assert len(chunks) == 3

    # 片段编号必须连续。
    assert [
        chunk.chunk_index
        for chunk in chunks
    ] == [
        0,
        1,
        2,
    ]

    # 遍历全部片段。
    for chunk in chunks:
        # 每个片段都不能超过最大长度。
        assert chunk.character_count <= 50

        # 根据 offset 从原文截取的内容，
        # 必须与片段保存的内容完全一致。
        assert chunk.content == content[
            chunk.start_offset:
            chunk.end_offset
        ]

        # 验证每个片段的 SHA-256 指纹。
        expected_hash = sha256(
            chunk.content.encode(
                "utf-8"
            )
        ).hexdigest()

        assert (
            chunk.content_hash
            == expected_hash
        )

    # zip() 将前后相邻的两个片段配对。
    for previous_chunk, current_chunk in zip(
        chunks,
        chunks[1:],
    ):
        # 前一个片段最后 10 个字符，
        # 应该等于后一个片段最前面的 10 个字符。
        assert (
            previous_chunk.content[-10:]
            == current_chunk.content[:10]
        )


# 验证切块器会优先在中文句号位置截断。
def test_chunker_prefers_sentence_boundary() -> None:
    # 第一个句号出现在第 35 个字符之后。
    # 最大长度为 60，所以它位于允许使用的后半区域。
    content = (
        ("甲" * 35)
        + "。"
        + ("乙" * 50)
    )

    # 创建测试文档。
    document = build_cleaned_document(
        content
    )

    # 执行切块。
    chunks = chunk_document(
        document,
        max_characters=60,
        overlap_characters=10,
    )

    # 正文应该被拆成至少两个片段。
    assert len(chunks) >= 2

    # 第一段应该在中文句号后结束，
    # 而不是强行截取到第 60 个字符。
    assert chunks[0].content == (
        ("甲" * 35) + "。"
    )

    # 句号位于索引 35，
    # 结束位置采用右开区间，所以是 36。
    assert chunks[0].end_offset == 36

    # 第二段应该从第一段结尾向前重叠 10 个字符。
    assert chunks[1].start_offset == 26

    # 两段的重叠内容应该完全一致。
    assert (
        chunks[0].content[-10:]
        == chunks[1].content[:10]
    )


# 使用多组参数验证非法配置。
@pytest.mark.parametrize(
    (
        "max_characters",
        "overlap_characters",
        "expected_message",
    ),
    [
        # 最大长度不能为 0。
        (
            0,
            0,
            "最大字符数",
        ),

        # 重叠长度不能是负数。
        (
            50,
            -1,
            "不能小于 0",
        ),

        # 重叠长度不能等于最大长度。
        (
            50,
            50,
            "必须小于",
        ),

        # 重叠长度也不能超过最大长度。
        (
            50,
            60,
            "必须小于",
        ),
    ],
)
def test_chunk_document_rejects_invalid_parameters(
    max_characters: int,
    overlap_characters: int,
    expected_message: str,
) -> None:
    # 准备一篇有效文档，
    # 确保测试失败原因只来自切块参数。
    document = build_cleaned_document(
        "这是一篇有效的测试文档。"
    )

    # 验证不同非法参数都会抛出业务异常。
    with pytest.raises(
        DocumentChunkingError,
        match=expected_message,
    ):
        chunk_document(
            document,
            max_characters=max_characters,
            overlap_characters=(
                overlap_characters
            ),
        )


# 验证空白正文无法生成片段。
def test_chunk_document_rejects_empty_content() -> None:
    # 手动构造一个无效的清洗结果，
    # 模拟上游校验被绕过的情况。
    document = build_cleaned_document(
        " \n\t "
    )

    # 切块器必须保护自己的输入边界。
    with pytest.raises(
        DocumentChunkingError,
        match="正文不能为空",
    ):
        chunk_document(
            document
        )