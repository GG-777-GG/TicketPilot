"""
测试知识文档预处理流水线。

与单元测试的区别：
- cleaning 测试只检查清洗器。
- chunking 测试只检查切块器。
- pipeline 测试检查清洗器和切块器能否正确协作。

测试流程：
RawDocument
→ prepare_document()
→ clean_document()
→ chunk_document()
→ PreparedDocument

测试目标：
- 原始文档能够完成清洗和切块。
- 清洗后的内容会被切块器使用。
- 片段顺序、offset 和重叠保持正确。
- 清洗异常能够继续向上传递。
- 切块异常能够继续向上传递。
"""

# pytest 用来验证预期异常。
import pytest

# 导入切块阶段可能抛出的异常。
from ticketpilot.knowledge.domain.chunking import (
    DocumentChunkingError,
)

# 导入清洗阶段可能抛出的异常。
from ticketpilot.knowledge.domain.cleaning import (
    DocumentCleaningError,
)

# 导入原始文档对象。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
    RawDocument,
    SourcePage,
)

# 导入完整文档预处理入口。
from ticketpilot.knowledge.domain.pipeline import (
    PreparedDocument,
    prepare_document,
)


# 验证一篇原始文档能够完成清洗和切块。
def test_prepare_document_cleans_and_chunks_text() -> None:
    # 使用重复文字生成需要拆分的正文。
    # 末尾加入零宽字符，用来验证清洗器确实参与流程。
    raw_content = (
        ("密码重置流程" * 30)
        + "\u200b"
    )

    # 标题使用全角英文字母，
    # 用来验证标题也经过清洗。
    raw_document = RawDocument(
        title="ＴｉｃｋｅｔＰｉｌｏｔ 密码帮助",
        content=raw_content,
    )

    # 使用较小的切块参数，
    # 让测试不需要准备特别长的文章。
    prepared_document = prepare_document(
        raw_document,
        max_characters=30,
        overlap_characters=5,
    )

    # 返回值应该是完整的预处理结果。
    assert isinstance(
        prepared_document,
        PreparedDocument,
    )

    # 全角英文字母应该已经转换成半角形式。
    assert (
        prepared_document.cleaned_document.title
        == "TicketPilot 密码帮助"
    )

    # 原始正文必须完整保留，包括零宽字符。
    assert (
        prepared_document
        .cleaned_document
        .raw_content
        == raw_content
    )

    # 清洗后的正文不应该再包含零宽字符。
    assert "\u200b" not in (
        prepared_document
        .cleaned_document
        .cleaned_content
    )

    # 当前正文应该产生多个知识片段。
    assert prepared_document.chunk_count > 1

    # chunks 使用 tuple，防止后续代码随意追加片段。
    assert isinstance(
        prepared_document.chunks,
        tuple,
    )

    # 取得清洗后正文，供后续 offset 验证。
    cleaned_content = (
        prepared_document
        .cleaned_document
        .cleaned_content
    )

    # 验证每一个片段的编号和原文位置。
    for expected_index, chunk in enumerate(
        prepared_document.chunks
    ):
        # chunk_index 必须从 0 开始连续排列。
        assert (
            chunk.chunk_index
            == expected_index
        )

        # 根据 offset 从清洗后正文截取的文字，
        # 必须等于片段保存的正文。
        assert chunk.content == (
            cleaned_content[
                chunk.start_offset:
                chunk.end_offset
            ]
        )

        # 每个片段都不能超过本次指定的 30 个字符。
        assert chunk.character_count <= 30

    # 遍历前后相邻的知识片段。
    for previous_chunk, current_chunk in zip(
        prepared_document.chunks,
        prepared_document.chunks[1:],
    ):
        # 因为正文中没有空白边界，
        # 所以相邻片段应该准确重叠 5 个字符。
        assert (
            previous_chunk.content[-5:]
            == current_chunk.content[:5]
        )


def test_prepare_document_keeps_pdf_pages_through_cleaning_and_chunking() -> None:
    """上传页码经过清洗和切块后，跨页片段仍保留两个来源页。"""

    raw_document = RawDocument(
        title="维修合同",
        content="甲  方\n\n保修三个月",
        source_type=KnowledgeSourceType.FILE,
        source_uri="contract.pdf",
        source_pages=(
            SourcePage(page_number=1, text="甲  方"),
            SourcePage(page_number=2, text="保修三个月"),
        ),
    )

    prepared = prepare_document(
        raw_document,
        max_characters=50,
        overlap_characters=0,
    )

    assert prepared.cleaned_document.cleaned_content == "甲 方\n\n保修三个月"
    assert prepared.chunk_count == 1
    assert prepared.chunks[0].page_numbers == (1, 2)


# 验证清洗异常不会被流水线隐藏。
def test_prepare_document_propagates_cleaning_error() -> None:
    # 正文清洗后会变成空字符串。
    raw_document = RawDocument(
        title="无效文档",
        content=" \t\r\n\u200b ",
    )

    # prepare_document() 不应该伪造成功结果，
    # 而应将清洗器的业务异常继续向上传递。
    with pytest.raises(
        DocumentCleaningError,
        match="正文",
    ):
        prepare_document(
            raw_document
        )


# 验证切块参数异常不会被流水线隐藏。
def test_prepare_document_propagates_chunking_error() -> None:
    # 准备一篇内容有效的原始文档。
    raw_document = RawDocument(
        title="有效文档",
        content="这是一篇可以正常完成清洗的文档。",
    )

    # overlap 与 max 相等，不符合切块要求。
    with pytest.raises(
        DocumentChunkingError,
        match="必须小于",
    ):
        prepare_document(
            raw_document,
            max_characters=20,
            overlap_characters=20,
        )
