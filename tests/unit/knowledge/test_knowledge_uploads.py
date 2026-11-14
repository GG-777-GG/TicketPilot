"""测试知识文件上传服务。"""

import pytest
from unittest.mock import Mock

from tests.factories.docx import build_docx
from ticketpilot.knowledge.parsers import (
    KnowledgeFileParserRegistry,
    UnsupportedKnowledgeFileTypeError,
)
from ticketpilot.knowledge.application.uploads import (
    KnowledgeFileUploadService,
    KnowledgeFileValidationError,
)


def test_validate_upload_selects_parser_without_parsing() -> None:
    # 使用测试替身，明确断言既不解析，也不触发解析器内的 OCR。
    registry = Mock(spec=KnowledgeFileParserRegistry)
    service = KnowledgeFileUploadService(parser_registry=registry)
    assert service.validate_upload(
        filename="scan.pdf", content_type="application/pdf", content=b"pending"
    ) is None
    registry.get_parser.assert_called_once_with(
        filename="scan.pdf", content_type="application/pdf"
    )
    registry.parse.assert_not_called()
    registry.get_parser.return_value.parse.assert_not_called()


@pytest.mark.parametrize(
    ("filename", "content"),
    [("", b"x"), ("folder/file.txt", b"x"), ("file.txt", b""), ("file.txt", b"123456")],
)
def test_validate_upload_rejects_invalid_input_before_parser(filename, content) -> None:
    registry = Mock(spec=KnowledgeFileParserRegistry)
    service = KnowledgeFileUploadService(parser_registry=registry, max_file_size_bytes=5)
    with pytest.raises(KnowledgeFileValidationError):
        service.validate_upload(filename=filename, content_type=None, content=content)
    registry.get_parser.assert_not_called()
    registry.parse.assert_not_called()


def test_validate_upload_rejects_unsupported_type() -> None:
    with pytest.raises(UnsupportedKnowledgeFileTypeError):
        KnowledgeFileUploadService().validate_upload(
            filename="old.doc", content_type=None, content=b"content"
        )


def test_validate_upload_does_not_claim_pdf_content_is_valid() -> None:
    # 上传检查接受受支持的名称，但格式是否损坏要交给后续解析。
    assert KnowledgeFileUploadService().validate_upload(
        filename="damaged.pdf", content_type="application/pdf", content=b"not a pdf"
    ) is None


class TestKnowledgeFileUploadService:
    """测试上传文件的安全检查和解析流程。"""

    def test_parse_upload_returns_parsed_text_file(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        result = service.parse_upload(
            filename="password-guide.txt",
            content_type="text/plain",
            content="忘记密码后联系管理员。".encode(
                "utf-8"
            ),
        )

        assert result.text == "忘记密码后联系管理员。"
        assert result.title == "password-guide"

    def test_parse_upload_returns_parsed_docx_file(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        result = service.parse_upload(
            filename="password-guide.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=build_docx("忘记密码请联系管理员。"),
        )

        assert result.text == "忘记密码请联系管理员。"
        assert result.title == "password-guide"

    @pytest.mark.parametrize(
        "filename",
        [
            "",
            "   ",
        ],
    )
    def test_parse_upload_rejects_missing_filename(
        self,
        filename: str,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            KnowledgeFileValidationError,
            match="必须包含文件名",
        ):
            service.parse_upload(
                filename=filename,
                content_type="text/plain",
                content=b"knowledge",
            )

    def test_parse_upload_rejects_null_character_in_filename(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            KnowledgeFileValidationError,
            match="文件名不合法",
        ):
            service.parse_upload(
                filename="bad\x00name.txt",
                content_type="text/plain",
                content=b"knowledge",
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "folder/guide.txt",
            "folder\\guide.txt",
        ],
    )
    def test_parse_upload_rejects_directory_path(
        self,
        filename: str,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            KnowledgeFileValidationError,
            match="不能包含目录路径",
        ):
            service.parse_upload(
                filename=filename,
                content_type="text/plain",
                content=b"knowledge",
            )

    def test_parse_upload_rejects_filename_longer_than_255_chars(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            KnowledgeFileValidationError,
            match="不能超过 255 个字符",
        ):
            service.parse_upload(
                filename=f"{'a' * 252}.txt",
                content_type="text/plain",
                content=b"knowledge",
            )

    def test_parse_upload_rejects_empty_content(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            KnowledgeFileValidationError,
            match="文件不能为空",
        ):
            service.parse_upload(
                filename="empty.txt",
                content_type="text/plain",
                content=b"",
            )

    def test_parse_upload_uses_configured_size_limit(
        self,
    ) -> None:
        service = KnowledgeFileUploadService(
            max_file_size_bytes=5,
        )

        with pytest.raises(
            KnowledgeFileValidationError,
            match="超过允许的大小",
        ):
            service.parse_upload(
                filename="large.txt",
                content_type="text/plain",
                content=b"123456",
            )

    def test_parse_upload_rejects_unsupported_file_type(
        self,
    ) -> None:
        service = KnowledgeFileUploadService()

        with pytest.raises(
            UnsupportedKnowledgeFileTypeError,
            match="暂不支持该文件类型",
        ):
            service.parse_upload(
                filename="manual.doc",
                content_type="application/msword",
                content=b"unsupported",
            )
