"""负责根据上传文件自动选择正确的文件解析器。

业务流程：
收到文件名和 Content-Type
→ 依次询问每个解析器是否支持
→ 找到后调用对应解析器
→ 没有解析器支持时返回明确错误
"""

from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParser,
    ParsedKnowledgeFile,
)
from ticketpilot.knowledge.parsers.markdown import (
    MarkdownParser,
)
from ticketpilot.knowledge.parsers.text import (
    PlainTextParser,
)
from ticketpilot.knowledge.parsers.pdf import (
    PdfParser,
)
from ticketpilot.knowledge.parsers.docx import (
    DocxParser,
)

class UnsupportedKnowledgeFileTypeError(
    ValueError
):
    """表示当前系统不支持上传的文件类型。"""


class KnowledgeFileParserRegistry:
    """统一保存和选择知识文件解析器。"""

    def __init__(
        self,
        parsers: tuple[
            KnowledgeFileParser,
            ...,
        ]
        | None = None,
    ) -> None:
        # 测试可以传入假的解析器。
        # 正式运行时使用系统默认解析器。
        if parsers is None:
            parsers = (
                PlainTextParser(),
                MarkdownParser(),
                PdfParser(),
                DocxParser(),
            )

        self._parsers = parsers

    # 这个函数负责寻找支持当前文件的解析器。
    def get_parser(
        self,
        *,
        filename: str,
        content_type: str | None,
    ) -> KnowledgeFileParser:
        # 依次询问每一个解析器是否支持这个文件。
        for parser in self._parsers:
            if parser.supports(
                filename=filename,
                content_type=content_type,
            ):
                return parser

        # 所有解析器都不支持时，抛出业务异常。
        raise UnsupportedKnowledgeFileTypeError(
            f"暂不支持该文件类型：{filename}"
        )

    # 这个函数负责选择解析器并解析文件。
    def parse(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ParsedKnowledgeFile:
        parser = self.get_parser(
            filename=filename,
            content_type=content_type,
        )

        return parser.parse(
            filename=filename,
            content=content,
        )