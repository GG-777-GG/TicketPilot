"""测试知识文件解析器及解析器注册表。"""

from io import BytesIO

import pytest
from docx import Document

from tests.factories.docx import build_docx
from tests.factories.pdf import build_pdf
from ticketpilot.knowledge.parsers import (
    KnowledgeFileParserRegistry,
    KnowledgeFileParsingError,
    MarkdownParser,
    PlainTextParser,
    UnsupportedKnowledgeFileTypeError,
)
from ticketpilot.knowledge.parsers.docx import DocxParser
from ticketpilot.knowledge.parsers.base import ParsedKnowledgePage
from ticketpilot.knowledge.parsers.pdf import PdfParser


class FakePdfPageOcr:
    """记录解析器交给 OCR 的页号，并返回预设的识别文字。"""

    def __init__(self, results: dict[int, str]) -> None:
        self.results = results
        self.calls: list[tuple[bytes, tuple[int, ...]]] = []

    def extract_pages(
        self,
        *,
        content: bytes,
        page_indices: tuple[int, ...],
    ) -> dict[int, str]:
        self.calls.append((content, page_indices))
        return self.results


class TestPlainTextParser:
    """测试 TXT 文件解析器。"""

    def test_supports_txt_extension_case_insensitively(
        self,
    ) -> None:
        parser = PlainTextParser()

        assert parser.supports(
            filename="guide.TXT",
            content_type="text/plain",
        )
        assert not parser.supports(
            filename="guide.md",
            content_type="text/markdown",
        )

    def test_parse_returns_text_and_filename_as_title(
        self,
    ) -> None:
        parser = PlainTextParser()

        result = parser.parse(
            filename="password-guide.txt",
            content="员工忘记密码后联系管理员。".encode(
                "utf-8-sig"
            ),
        )

        assert result.text == "员工忘记密码后联系管理员。"
        assert result.title == "password-guide"

    def test_parse_rejects_empty_file(
        self,
    ) -> None:
        parser = PlainTextParser()

        with pytest.raises(
            KnowledgeFileParsingError,
            match="内容不能为空",
        ):
            parser.parse(
                filename="empty.txt",
                content=b"  \n\t",
            )

    def test_parse_rejects_non_utf8_file(
        self,
    ) -> None:
        parser = PlainTextParser()

        with pytest.raises(
            KnowledgeFileParsingError,
            match="UTF-8",
        ):
            parser.parse(
                filename="invalid.txt",
                content=b"\xff\xfe\xfa",
            )


class TestMarkdownParser:
    """测试 Markdown 文件解析器。"""

    def test_supports_markdown_extensions(
        self,
    ) -> None:
        parser = MarkdownParser()

        assert parser.supports(
            filename="guide.md",
            content_type="text/markdown",
        )
        assert parser.supports(
            filename="guide.MARKDOWN",
            content_type="text/markdown",
        )
        assert not parser.supports(
            filename="guide.txt",
            content_type="text/plain",
        )

    def test_parse_uses_first_level_heading_as_title(
        self,
    ) -> None:
        parser = MarkdownParser()
        content = "说明文字\n# 密码重置指南\n联系管理员。"

        result = parser.parse(
            filename="guide.md",
            content=content.encode("utf-8"),
        )

        assert result.text == content
        assert result.title == "密码重置指南"

    def test_parse_falls_back_to_filename_for_title(
        self,
    ) -> None:
        parser = MarkdownParser()

        result = parser.parse(
            filename="account-help.md",
            content="员工账号使用说明".encode("utf-8"),
        )

        assert result.title == "account-help"

    def test_parse_rejects_empty_file(
        self,
    ) -> None:
        parser = MarkdownParser()

        with pytest.raises(
            KnowledgeFileParsingError,
            match="内容不能为空",
        ):
            parser.parse(
                filename="empty.md",
                content=b"\n\n",
            )


class TestPdfParser:
    """测试文字型 PDF、扫描页 OCR 和错误边界。"""

    def test_supports_pdf_extension_case_insensitively(
        self,
    ) -> None:
        parser = PdfParser()

        assert parser.supports(filename="guide.PDF", content_type=None)
        assert not parser.supports(filename="guide.txt", content_type="application/pdf")

    def test_parse_joins_text_from_nonempty_pages(
        self,
    ) -> None:
        # 空白页会交给 OCR；假的 OCR 返回空字符串，避免单元测试加载真实模型。
        ocr = FakePdfPageOcr({1: ""})
        parser = PdfParser(ocr=ocr)
        content = build_pdf("First page", "", "Third page")

        result = parser.parse(
            filename="password-guide.pdf",
            content=content,
        )

        assert result.text == "First page\n\nThird page"
        assert result.title == "password-guide"
        assert result.pages == (
            ParsedKnowledgePage(page_number=1, text="First page"),
            ParsedKnowledgePage(page_number=3, text="Third page"),
        )
        assert ocr.calls == [(content, (1,))]

    def test_parse_does_not_call_ocr_for_text_only_pdf(self) -> None:
        ocr = FakePdfPageOcr({})

        result = PdfParser(ocr=ocr).parse(
            filename="text.pdf",
            content=build_pdf("First page", "Second page"),
        )

        assert result.text == "First page\n\nSecond page"
        assert result.pages == (
            ParsedKnowledgePage(page_number=1, text="First page"),
            ParsedKnowledgePage(page_number=2, text="Second page"),
        )
        assert ocr.calls == []

    def test_parse_uses_ocr_for_scanned_page(self) -> None:
        ocr = FakePdfPageOcr({0: "扫描页的文字"})
        content = build_pdf("")

        result = PdfParser(ocr=ocr).parse(
            filename="scan.pdf",
            content=content,
        )

        assert result.text == "扫描页的文字"
        assert result.title == "scan"
        assert result.pages == (
            ParsedKnowledgePage(page_number=1, text="扫描页的文字"),
        )
        assert ocr.calls == [(content, (0,))]

    def test_parse_preserves_page_order_for_mixed_pdf(self) -> None:
        ocr = FakePdfPageOcr({1: "第二页扫描文字", 3: "第四页扫描文字"})

        result = PdfParser(ocr=ocr).parse(
            filename="mixed.pdf",
            content=build_pdf("First page", "", "Third page", ""),
        )

        assert result.text == (
            "First page\n\n第二页扫描文字\n\n"
            "Third page\n\n第四页扫描文字"
        )
        assert result.pages == (
            ParsedKnowledgePage(page_number=1, text="First page"),
            ParsedKnowledgePage(page_number=2, text="第二页扫描文字"),
            ParsedKnowledgePage(page_number=3, text="Third page"),
            ParsedKnowledgePage(page_number=4, text="第四页扫描文字"),
        )
        assert ocr.calls[0][1] == (1, 3)

    def test_parse_rejects_missing_pdf_marker(
        self,
    ) -> None:
        with pytest.raises(KnowledgeFileParsingError, match="有效的 PDF"):
            PdfParser().parse(filename="bad.pdf", content=b"not a PDF")

    def test_parse_rejects_damaged_pdf(
        self,
    ) -> None:
        with pytest.raises(KnowledgeFileParsingError, match="损坏或格式不受支持"):
            PdfParser().parse(filename="bad.pdf", content=b"%PDF-corrupt")

    def test_parse_rejects_encrypted_pdf(
        self,
    ) -> None:
        with pytest.raises(KnowledgeFileParsingError, match="加密 PDF"):
            PdfParser().parse(
                filename="secret.pdf",
                content=build_pdf("Secret", password="test-password"),
            )

    def test_parse_rejects_more_than_100_pages(
        self,
    ) -> None:
        with pytest.raises(KnowledgeFileParsingError, match="100 页"):
            PdfParser().parse(
                filename="long.pdf",
                content=build_pdf(*([""] * 101)),
            )

    def test_parse_rejects_extracted_text_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "ticketpilot.knowledge.parsers.pdf.MAX_EXTRACTED_CHARACTERS",
            5,
        )

        with pytest.raises(KnowledgeFileParsingError, match="文字超过限制"):
            PdfParser().parse(
                filename="large.pdf",
                content=build_pdf("Too much text"),
            )

    def test_parse_rejects_pdf_without_extractable_or_ocr_text(
        self,
    ) -> None:
        with pytest.raises(KnowledgeFileParsingError, match="OCR 可识别"):
            PdfParser(ocr=FakePdfPageOcr({0: ""})).parse(
                filename="scan.pdf",
                content=build_pdf(""),
            )

    def test_parse_rejects_more_than_30_ocr_pages(self) -> None:
        ocr = FakePdfPageOcr({})

        with pytest.raises(KnowledgeFileParsingError, match="30 页"):
            PdfParser(ocr=ocr).parse(
                filename="scan.pdf",
                content=build_pdf(*([""] * 31)),
            )

        assert ocr.calls == []

    def test_parse_counts_ocr_text_toward_character_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "ticketpilot.knowledge.parsers.pdf.MAX_EXTRACTED_CHARACTERS",
            5,
        )

        with pytest.raises(KnowledgeFileParsingError, match="文字超过限制"):
            PdfParser(ocr=FakePdfPageOcr({1: "Too much text"})).parse(
                filename="mixed.pdf",
                content=build_pdf("Hi", ""),
            )


class TestDocxParser:
    """测试 DOCX 段落、表格和错误边界。"""

    def test_supports_docx_extension_case_insensitively(
        self,
    ) -> None:
        parser = DocxParser()

        assert parser.supports(filename="guide.DOCX", content_type=None)
        assert not parser.supports(filename="guide.doc", content_type=None)

    def test_parse_preserves_paragraph_and_table_order(
        self,
    ) -> None:
        result = DocxParser().parse(
            filename="password-guide.docx",
            content=build_docx(
                "First paragraph",
                [["Name", "Team"], ["Alice", "Support"]],
                "Last paragraph",
            ),
        )

        assert result.text == (
            "First paragraph\n\n"
            "Name | Team\n\n"
            "Alice | Support\n\n"
            "Last paragraph"
        )
        assert result.title == "password-guide"

    def test_parse_preserves_empty_cell_positions_and_skips_empty_rows(
        self,
    ) -> None:
        result = DocxParser().parse(
            filename="guide.docx",
            content=build_docx(
                "",
                [["Name", "", "Team"], ["  ", "  ", "  "], ["", "Contact IT", ""]],
                "  ",
            ),
        )

        assert result.text == "Name |  | Team\n\n| Contact IT |"

    def test_parse_does_not_repeat_horizontally_merged_cell_text(
        self,
    ) -> None:
        document = Document()
        table = document.add_table(rows=2, cols=3)
        for index, heading in enumerate(("Item", "Price", "Total")):
            table.cell(0, index).text = heading
        table.cell(1, 0).merge(table.cell(1, 2)).text = "Grand total"
        output = BytesIO()
        document.save(output)

        result = DocxParser().parse(
            filename="merged.docx",
            content=output.getvalue(),
        )

        assert result.text == "Item | Price | Total\n\nGrand total |  |"

    def test_parse_keeps_equal_text_in_distinct_cells(
        self,
    ) -> None:
        result = DocxParser().parse(
            filename="repeated-values.docx",
            content=build_docx([["Same", "Same"]]),
        )

        assert result.text == "Same | Same"

    def test_parse_rejects_invalid_docx(
        self,
    ) -> None:
        with pytest.raises(
            KnowledgeFileParsingError,
            match="损坏或格式不受支持",
        ):
            DocxParser().parse(
                filename="broken.docx",
                content=b"not a DOCX archive",
            )

    def test_parse_rejects_document_without_text(
        self,
    ) -> None:
        with pytest.raises(
            KnowledgeFileParsingError,
            match="没有可提取的文字",
        ):
            DocxParser().parse(
                filename="empty.docx",
                content=build_docx("", "  "),
            )

    def test_parse_rejects_extracted_text_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "ticketpilot.knowledge.parsers.docx.MAX_DOCX_CHARACTERS",
            5,
        )

        with pytest.raises(
            KnowledgeFileParsingError,
            match="文字超过限制",
        ):
            DocxParser().parse(
                filename="large.docx",
                content=build_docx("Too much text"),
            )

    def test_parse_rejects_too_many_archive_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "ticketpilot.knowledge.parsers.docx.MAX_DOCX_ARCHIVE_ENTRIES",
            1,
        )

        with pytest.raises(
            KnowledgeFileParsingError,
            match="内部文件数量超过限制",
        ):
            DocxParser().parse(
                filename="many-parts.docx",
                content=build_docx("Hello"),
            )

    def test_parse_rejects_archive_over_uncompressed_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "ticketpilot.knowledge.parsers.docx.MAX_DOCX_UNCOMPRESSED_BYTES",
            1,
        )

        with pytest.raises(
            KnowledgeFileParsingError,
            match="解压后大小超过限制",
        ):
            DocxParser().parse(
                filename="expanded.docx",
                content=build_docx("Hello"),
            )


class TestKnowledgeFileParserRegistry:
    """测试解析器注册表。"""

    @pytest.mark.parametrize(
        ("filename", "expected_parser_type"),
        [
            ("guide.txt", PlainTextParser),
            ("guide.md", MarkdownParser),
            ("guide.markdown", MarkdownParser),
            ("guide.pdf", PdfParser),
            ("guide.docx", DocxParser),
        ],
    )
    def test_get_parser_selects_matching_parser(
        self,
        filename: str,
        expected_parser_type: type[
            PlainTextParser | MarkdownParser | PdfParser | DocxParser
        ],
    ) -> None:
        registry = KnowledgeFileParserRegistry()

        parser = registry.get_parser(
            filename=filename,
            content_type=None,
        )

        assert isinstance(
            parser,
            expected_parser_type,
        )

    def test_parse_delegates_to_matching_parser(
        self,
    ) -> None:
        registry = KnowledgeFileParserRegistry()

        result = registry.parse(
            filename="guide.txt",
            content_type="text/plain",
            content="知识库内容".encode("utf-8"),
        )

        assert result.text == "知识库内容"
        assert result.title == "guide"

    def test_get_parser_rejects_unsupported_file(
        self,
    ) -> None:
        registry = KnowledgeFileParserRegistry()

        with pytest.raises(
            UnsupportedKnowledgeFileTypeError,
            match="暂不支持该文件类型",
        ):
            registry.get_parser(
                filename="manual.doc",
                content_type="application/msword",
            )
