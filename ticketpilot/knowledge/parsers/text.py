"""负责把 TXT 文件解析成知识库可以处理的纯文本。

业务流程：
接收 TXT 文件的 bytes
→ 使用 UTF-8 解码
→ 检查文件是否为空
→ 返回 ParsedKnowledgeFile
"""

from pathlib import Path

from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParsingError,
    ParsedKnowledgeFile,
)


class PlainTextParser:
    """负责解析 UTF-8 编码的 TXT 文件。"""

    # 这个函数负责判断当前解析器是否支持指定文件。
    def supports(
        self,
        *,
        filename: str,
        content_type: str | None,
    ) -> bool:
        # Path(filename).suffix 可以取得文件扩展名。
        # 例如：notice.TXT → .TXT → .txt
        suffix = Path(
            filename
        ).suffix.lower()

        return suffix == ".txt"

    # 这个函数负责把 TXT 文件转换成纯文本。
    def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> ParsedKnowledgeFile:
        try:
            # utf-8-sig 既能读取普通 UTF-8，
            # 也能自动去掉 Windows 文件可能携带的 BOM。
            text = content.decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError as error:
            raise KnowledgeFileParsingError(
                "TXT 文件必须使用 UTF-8 编码"
            ) from error

        # 文件可能有内容，但全部都是空格或换行。
        if not text.strip():
            raise KnowledgeFileParsingError(
                "TXT 文件内容不能为空"
            )

        return ParsedKnowledgeFile(
            text=text,

            # example.txt 的 stem 是 example。
            title=Path(
                filename
            ).stem,
        )