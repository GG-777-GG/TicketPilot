"""把 Word DOCX 文件解析成知识库可处理的纯文本。

业务流程：
接收 DOCX 文件
→ 检查文件内容
→ 提取段落和表格文字
→ 返回 ParsedKnowledgeFile
"""

from pathlib import Path
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParsingError,
    ParsedKnowledgeFile,
)

MAX_DOCX_CHARACTERS = 500_000

# 压缩包内部最多允许 1000 个文件。
MAX_DOCX_ARCHIVE_ENTRIES = 1000

# 内部文件声明的解压后总大小最多 30 MiB。
MAX_DOCX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024

class DocxParser:
    """负责解析 .docx 格式的 Word 文档。"""

    # 这个函数负责判断文件扩展名是不是 DOCX。
    def supports(
        self,
        *,
        filename: str,
        content_type: str | None,
    ) -> bool:
        return Path(filename).suffix.lower() == ".docx"

    # 这个函数负责按原顺序提取正文段落和表格文字。
    def parse(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> ParsedKnowledgeFile:
        try:
            # 先读取 DOCX 压缩包的目录，不提取文件。
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()

                # 文件条目太多时拒绝处理。
                if len(entries) > MAX_DOCX_ARCHIVE_ENTRIES:
                    raise KnowledgeFileParsingError(
                        "DOCX 内部文件数量超过限制"
                    )

                # file_size 是每个内部文件声明的解压后大小。
                uncompressed_bytes = sum(
                    entry.file_size
                    for entry in entries
                )

                if uncompressed_bytes > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise KnowledgeFileParsingError(
                        "DOCX 解压后大小超过限制"
                    )

            # 通过初步检查后，才交给 python-docx 解析。
            document = Document(BytesIO(content))
        except KnowledgeFileParsingError:
            raise
        except (
            BadZipFile,
            PackageNotFoundError,
            ValueError,
            KeyError,
        ) as error:
            raise KnowledgeFileParsingError(
                "DOCX 文件损坏或格式不受支持"
            ) from error

        texts: list[str] = []
        total_characters = 0

        # 按文档中的出现顺序遍历段落和表格。
        for block in document.iter_inner_content():
            candidates: list[str] = []

            if isinstance(block, Paragraph):
                candidates.append(block.text)

            elif isinstance(block, Table):
                for row in block.rows:
                    cells: list[str] = []
                    remaining_merged_columns = 0

                    for cell in row.cells:
                        # 合并单元格后续占据的列，保留空位，但不重复文字。
                        if remaining_merged_columns > 0:
                            cells.append("")
                            remaining_merged_columns -= 1
                            continue

                        cells.append(cell.text.strip())

                        # grid_span 表示这个单元格横向占据几列。
                        remaining_merged_columns = cell.grid_span - 1

                    # 整行为空则跳过；非空行保留列位置。
                    if any(cells):
                        candidates.append(" | ".join(cells))

            for candidate in candidates:
                text = candidate.strip()
                if not text:
                    continue

                total_characters += len(text)
                if total_characters > MAX_DOCX_CHARACTERS:
                    raise KnowledgeFileParsingError(
                        "DOCX 提取后的文字超过限制"
                    )

                texts.append(text)

        if not texts:
            raise KnowledgeFileParsingError(
                "DOCX 没有可提取的文字"
            )

        return ParsedKnowledgeFile(
            text="\n\n".join(texts),
            title=Path(filename).stem,
        )