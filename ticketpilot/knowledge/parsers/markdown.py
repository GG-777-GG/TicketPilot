"""负责把 Markdown 文件解析成知识库可以处理的文本。

业务流程：
接收 .md 或 .markdown 文件
→ 使用 UTF-8 解码
→ 尝试从一级标题中提取文档标题
→ 返回 Markdown 正文和标题
"""

from pathlib import Path

from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParsingError,
    ParsedKnowledgeFile,
)


class MarkdownParser:
    """负责解析 UTF-8 编码的 Markdown 文件。"""

    # 这个函数负责判断当前解析器是否支持指定文件。
    def supports(
        self,
        *,
        filename: str,
        content_type: str | None,
    ) -> bool:
        suffix = Path(
            filename
        ).suffix.lower()

        # Markdown 常见的两种扩展名。
        return suffix in {
            ".md",
            ".markdown",
        }

    # 这个函数负责解析 Markdown 文件。
    def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> ParsedKnowledgeFile:
        try:
            text = content.decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError as error:
            raise KnowledgeFileParsingError(
                "Markdown 文件必须使用 UTF-8 编码"
            ) from error

        if not text.strip():
            raise KnowledgeFileParsingError(
                "Markdown 文件内容不能为空"
            )

        # 优先使用 Markdown 中的一级标题。
        title = self._find_title(
            text
        )

        # 没有一级标题时，使用文件名作为标题。
        if title is None:
            title = Path(
                filename
            ).stem

        return ParsedKnowledgeFile(
            text=text,
            title=title,
        )

    # 这个函数负责寻找 Markdown 中第一个一级标题。
    def _find_title(
        self,
        text: str,
    ) -> str | None:
        # splitlines() 把整篇文本按行拆开。
        for line in text.splitlines():
            stripped_line = line.strip()

            # Markdown 一级标题的格式是：# 标题
            if stripped_line.startswith(
                "# "
            ):
                title = stripped_line.removeprefix(
                    "# "
                ).strip()

                if title:
                    return title

        return None