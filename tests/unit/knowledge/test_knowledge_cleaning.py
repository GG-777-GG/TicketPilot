"""
测试知识库第一阶段的基础文档清洗器。

测试目标：
- 验证全角字符能够转换成统一形式。
- 验证 Windows 换行符能够统一。
- 验证多余空格和空白行能够被清理。
- 验证不可见字符能够被删除。
- 验证原始内容不会丢失。
- 验证内容指纹稳定且正确。
- 验证无效文档会被拒绝。

清洗器属于 RAG 入库的基础设施。
一旦清洗规则错误，后面的切块、Embedding 和检索都会受到影响，
因此每条清洗规则都需要通过自动化测试固定行为。
"""

# sha256 用来计算测试中的预期内容指纹。
from hashlib import sha256

# pytest 提供异常断言等测试工具。
import pytest

# 导入清洗器版本、异常和清洗函数。
from ticketpilot.knowledge.domain.cleaning import (
    CLEANER_VERSION,
    DocumentCleaningError,
    clean_document,
)

# 导入清洗前的文档对象和来源类型。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
    PageSpan,
    RawDocument,
    SourcePage,
)


# 验证基础字符、空格和换行清洗。
def test_clean_document_normalizes_text() -> None:
    # 准备包含全角字符和全角空格的标题。
    raw_title = (
        "  ＴｉｃｋｅｔＰｉｌｏｔ　密码重置  "
    )

    # 准备包含以下问题的正文：
    # - Windows 换行符
    # - 过多空白行
    # - 连续 Tab
    # - 零宽字符
    raw_content = (
        "第一段。\r\n"
        "\r\n"
        "\r\n"
        "第二段。\t\t说明\u200b"
    )

    # 创建一篇模拟网页来源的原始文档。
    raw_document = RawDocument(
        title=raw_title,
        content=raw_content,
        source_type=KnowledgeSourceType.URL,
        source_uri="https://example.com/password-reset",
    )

    # 执行文档清洗。
    cleaned_document = clean_document(
        raw_document
    )

    # 全角英文字母应该转换成半角形式。
    # 标题前后的空格也应该被删除。
    assert cleaned_document.title == (
        "TicketPilot 密码重置"
    )

    # Windows 换行应该转换成 \n。
    # 三个空白行应压缩成一个空白行。
    # 连续 Tab 应转换成一个普通空格。
    # 零宽字符应该被删除。
    assert cleaned_document.cleaned_content == (
        "第一段。\n\n第二段。 说明"
    )

    # 原始正文必须完整保留，不能被清洗结果覆盖。
    assert (
        cleaned_document.raw_content
        == raw_content
    )

    # 来源类型应该原样传递。
    assert cleaned_document.source_type == (
        KnowledgeSourceType.URL
    )

    # 来源地址也应该原样传递。
    assert cleaned_document.source_uri == (
        "https://example.com/password-reset"
    )

    # 清洗结果需要记录当前清洗器版本。
    assert cleaned_document.cleaner_version == (
        CLEANER_VERSION
    )
    assert cleaned_document.page_spans == ()


def test_clean_document_records_offsets_after_cleaning_each_page() -> None:
    """页范围必须按清洗后的文字长度计算，而不是按原文长度。"""

    raw_document = RawDocument(
        title="维修合同",
        content="甲  方\n\n 保修三个月 ",
        source_type=KnowledgeSourceType.FILE,
        source_uri="contract.pdf",
        source_pages=(
            SourcePage(page_number=1, text="甲  方"),
            SourcePage(page_number=2, text=" 保修三个月 "),
        ),
    )

    result = clean_document(raw_document)

    assert result.cleaned_content == "甲 方\n\n保修三个月"
    assert result.page_spans == (
        PageSpan(page_number=1, start_offset=0, end_offset=3),
        PageSpan(page_number=2, start_offset=5, end_offset=10),
    )
    assert result.raw_content == raw_document.content
    # 用范围切片取回各页文字，证明页码没有偏移。
    assert tuple(
        result.cleaned_content[span.start_offset:span.end_offset]
        for span in result.page_spans
    ) == ("甲 方", "保修三个月")


def test_clean_document_preserves_page_number_after_empty_cleaned_page() -> None:
    """清洗后为空的第二页不占范围，第三页仍标记为第 3 页。"""

    raw_document = RawDocument(
        title="三页 PDF",
        content="第一页\n\n\u200b\n\n第三页",
        source_type=KnowledgeSourceType.FILE,
        source_pages=(
            SourcePage(page_number=1, text="第一页"),
            SourcePage(page_number=2, text="\u200b"),
            SourcePage(page_number=3, text="第三页"),
        ),
    )

    result = clean_document(raw_document)

    assert result.cleaned_content == "第一页\n\n第三页"
    assert result.page_spans == (
        PageSpan(page_number=1, start_offset=0, end_offset=3),
        PageSpan(page_number=3, start_offset=5, end_offset=8),
    )


def test_clean_document_rejects_pages_with_no_cleaned_text() -> None:
    """有页面对象也不能让无有效正文的文件进入知识库。"""

    raw_document = RawDocument(
        title="空扫描件",
        content="\u200b",
        source_type=KnowledgeSourceType.FILE,
        source_pages=(SourcePage(page_number=1, text="\u200b"),),
    )

    with pytest.raises(DocumentCleaningError, match="正文"):
        clean_document(raw_document)


# 验证内容指纹确实来自清洗后的正文。
def test_clean_document_builds_content_hash() -> None:
    # 创建一篇正常的原始文档。
    raw_document = RawDocument(
        title="重置密码",
        content="用户可以通过邮箱重置密码。",
    )

    # 执行清洗。
    cleaned_document = clean_document(
        raw_document
    )

    # 使用和业务代码相同的规则计算预期指纹。
    expected_hash = sha256(
        cleaned_document.cleaned_content.encode(
            "utf-8"
        )
    ).hexdigest()

    # 清洗器保存的指纹必须与预期一致。
    assert (
        cleaned_document.content_hash
        == expected_hash
    )

    # SHA-256 的十六进制结果固定为 64 个字符。
    assert len(
        cleaned_document.content_hash
    ) == 64


# 验证格式不同但清洗结果相同的正文具有相同指纹。
def test_equivalent_cleaned_content_has_same_hash() -> None:
    # 第一份正文使用 Windows 换行符。
    first_document = RawDocument(
        title="第一份文档",
        content=(
            "密码重置\r\n"
            "\r\n"
            "操作步骤"
        ),
    )

    # 第二份正文使用统一的 Linux 换行符。
    second_document = RawDocument(
        title="第二份文档",
        content=(
            "密码重置\n"
            "\n"
            "操作步骤"
        ),
    )

    # 分别清洗两篇文档。
    first_result = clean_document(
        first_document
    )
    second_result = clean_document(
        second_document
    )

    # 两篇正文清洗后应该完全相同。
    assert (
        first_result.cleaned_content
        == second_result.cleaned_content
    )

    # 因此它们的内容指纹也应该相同。
    assert (
        first_result.content_hash
        == second_result.content_hash
    )


# 验证清洗后没有标题的文档会被拒绝。
def test_clean_document_rejects_empty_title() -> None:
    # 标题只包含空格和零宽字符。
    raw_document = RawDocument(
        title=" \t\u200b ",
        content="正文仍然存在。",
    )

    # 验证清洗器抛出指定业务异常。
    # match 会检查错误信息中是否包含“标题”。
    with pytest.raises(
        DocumentCleaningError,
        match="标题",
    ):
        clean_document(
            raw_document
        )


# 验证清洗后没有正文的文档会被拒绝。
def test_clean_document_rejects_empty_content() -> None:
    # 正文只包含空格、换行、空字符和零宽字符。
    raw_document = RawDocument(
        title="无效文档",
        content=" \t\r\n\u0000\u200b ",
    )

    # 清洗后正文为空时必须抛出业务异常。
    with pytest.raises(
        DocumentCleaningError,
        match="正文",
    ):
        clean_document(
            raw_document
        )
