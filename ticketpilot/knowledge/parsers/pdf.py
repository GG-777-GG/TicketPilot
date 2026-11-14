"""把 PDF 解析成知识库可处理的纯文本。

业务流程：
接收 PDF 的 bytes
→ 检查文件标识、加密状态和页数
→ 优先提取每页原有的文字
→ 对提取不到文字的页面执行 OCR
→ 按原页顺序生成文字和页码信息
→ 返回 ParsedKnowledgeFile
"""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ticketpilot.knowledge.parsers.base import (
    KnowledgeFileParsingError,
    ParsedKnowledgeFile,
    ParsedKnowledgePage,
)
from ticketpilot.knowledge.parsers.ocr import (
    MAX_OCR_PAGES,
    PdfPageOcr,
    RapidOcrPdfPageExtractor,
)


MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARACTERS = 500_000


class PdfParser:
    """负责解析文字型 PDF 和扫描版 PDF。"""

    def __init__(
        self,
        *,
        # 测试可以传入假的 OCR；不传时，遇到扫描页才创建真实 OCR。
        ocr: PdfPageOcr | None = None,
    ) -> None:
        self._ocr = ocr

    # 这个函数负责判断上传的文件是否为 PDF。
    def supports(
        self,
        *,
        # 来自上传请求，例如 "维修合同.pdf"。
        filename: str,

        # 注册表统一传入此参数；当前只依据扩展名判断。
        content_type: str | None,
    ) -> bool:
        return Path(filename).suffix.lower() == ".pdf"

    # 这个函数负责提取 PDF 文字，并记录文字来自哪一页。
    def parse(
        self,
        *,
        # 来自上传请求；这里用它生成默认标题。
        filename: str,

        # 上传文件的实际 bytes，不是磁盘路径。
        content: bytes,
    ) -> ParsedKnowledgeFile:
        if b"%PDF-" not in content[:1024]:
            raise KnowledgeFileParsingError(
                "文件内容不是有效的 PDF"
            )

        try:
            # PdfReader 接收可读取的文件对象，因此用 BytesIO 包装 bytes。
            reader = PdfReader(BytesIO(content))

            if reader.is_encrypted:
                raise KnowledgeFileParsingError(
                    "暂不支持加密 PDF"
                )

            if len(reader.pages) > MAX_PDF_PAGES:
                raise KnowledgeFileParsingError(
                    "PDF 页数不能超过 100 页"
                )

            # 列表下标与 PDF 页面一一对应；空白页先保留空字符串。
            page_texts: list[str] = []

            # 记录需要 OCR 的页号。这里从 0 开始，是程序下标。
            ocr_page_indices: list[int] = []

            total_characters = 0

            for page_index, page in enumerate(reader.pages):
                page_text = (
                    page.extract_text() or ""
                ).strip()

                page_texts.append(page_text)

                if not page_text:
                    ocr_page_indices.append(page_index)
                    continue

                total_characters += len(page_text)
                if total_characters > MAX_EXTRACTED_CHARACTERS:
                    raise KnowledgeFileParsingError(
                        "PDF 提取后的文字超过限制"
                    )

            if ocr_page_indices:
                if len(ocr_page_indices) > MAX_OCR_PAGES:
                    raise KnowledgeFileParsingError(
                        f"一份 PDF 最多对 {MAX_OCR_PAGES} 页执行 OCR"
                    )

                if self._ocr is not None:
                    extractor = self._ocr
                else:
                    try:
                        extractor = RapidOcrPdfPageExtractor()
                    except Exception as error:
                        raise KnowledgeFileParsingError(
                            "OCR 引擎无法启动"
                        ) from error

                ocr_results = extractor.extract_pages(
                    # OCR 需要整份 PDF 的 bytes，才能渲染指定页面。
                    content=content,

                    # 只传无法直接提取文字的页号。
                    page_indices=tuple(ocr_page_indices),
                )

                for page_index in ocr_page_indices:
                    ocr_text = (
                        ocr_results.get(page_index) or ""
                    ).strip()

                    total_characters += len(ocr_text)
                    if total_characters > MAX_EXTRACTED_CHARACTERS:
                        raise KnowledgeFileParsingError(
                            "PDF 提取后的文字超过限制"
                        )

                    # 将 OCR 结果放回它在 PDF 中原来的位置。
                    page_texts[page_index] = ocr_text

        except PdfReadError as error:
            raise KnowledgeFileParsingError(
                "PDF 文件损坏或格式不受支持"
            ) from error

        # 把每个非空页面转成“页码 + 文字”对象。
        pages = tuple(
            ParsedKnowledgePage(
                # enumerate 从 0 开始；展示给用户的页码从 1 开始。
                page_number=page_index + 1,
                text=page_text,
            )
            for page_index, page_text in enumerate(page_texts)
            if page_text
        )

        if not pages:
            raise KnowledgeFileParsingError(
                "PDF 没有可提取或 OCR 可识别的文字"
            )

        return ParsedKnowledgeFile(
            # 现有入库流程继续使用整篇文字。
            text="\n\n".join(page.text for page in pages),

            # 去掉 .pdf 扩展名，作为默认标题。
            title=Path(filename).stem,

            # 新增的逐页信息，供后续来源溯源使用。
            pages=pages,
        )