"""定义知识文件解析器必须遵守的统一接口。

业务流程：
上传文件
→ 选择对应的解析器
→ 提取纯文本及可用的来源位置
→ 交给知识库清洗和分块流程
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(
    frozen=True,
    slots=True,
)
class ParsedKnowledgePage:
    """表示 PDF 中一页已经提取或 OCR 识别的文字。"""

    # PDF 页码，从 1 开始，方便直接展示给用户。
    page_number: int

    # 这一页提取或识别出的文字。
    text: str


@dataclass(
    frozen=True,
    slots=True,
)
class ParsedKnowledgeFile:
    """表示整个文件解析完成后的统一结果。"""

    # 整份文件的文字；现有入库流程仍然读取这个字段。
    text: str

    # 从文件取得的标题；没有标题时可以是 None。
    title: str | None = None

    # 每一页的文字及页码。
    # TXT、Markdown、DOCX 暂时没有 PDF 页码，所以默认是空元组。
    pages: tuple[ParsedKnowledgePage, ...] = ()


class KnowledgeFileParsingError(ValueError):
    """表示上传的文件无法被正确解析。"""


class KnowledgeFileParser(Protocol):
    """规定每一种文件解析器必须提供哪些方法。"""

    # 判断解析器是否支持这个文件。
    def supports(
        self,
        *,
        filename: str,            # 上传文件的名称，例如 "合同.pdf"
        content_type: str | None,  # 上传请求声明的类型；有时可能为空
    ) -> bool:
        ...

    # 把上传文件的 bytes 解析成统一结果。
    def parse(
        self,
        *,
        filename: str,  # 用于判断文件类型、生成标题
        content: bytes, # 文件实际内容，不是文件路径
    ) -> ParsedKnowledgeFile:
        ...