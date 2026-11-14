"""把扫描版 PDF 的指定页面识别成文字。

业务流程：
接收 PDF 的 bytes 和需要识别的页号
→ 打开 PDF
→ 逐页渲染成图片
→ 用 RapidOCR 识别图片中的文字
→ 返回 {页号: 文字}
"""

from typing import Protocol

# PDFium 负责把 PDF 页面渲染成图片。
import pypdfium2 as pdfium

# RapidOCR 负责识别图片中的文字。
from rapidocr import RapidOCR

# 把底层库的错误转换成项目统一的文件解析错误。
from ticketpilot.knowledge.parsers.base import KnowledgeFileParsingError


# 一份 PDF 最多允许多少页进入 OCR，避免请求运行过久。
MAX_OCR_PAGES = 30

# 渲染时宽和高都放大 2 倍，让小字更容易识别。
PDF_RENDER_SCALE = 2

# 限制渲染后的单页像素数，避免异常大的页面占用过多内存。
MAX_RENDERED_PAGE_PIXELS = 12_000_000


class PdfPageOcr(Protocol):
    """规定 PDF 页面 OCR 实现必须提供的方法。"""

    # 这个函数负责识别指定 PDF 页面中的文字。
    def extract_pages(
        self,
        *,
        content: bytes,
        page_indices: tuple[int, ...],
    ) -> dict[int, str]:
        ...


class RapidOcrPdfPageExtractor:
    """使用 PDFium 和本地 RapidOCR 模型识别扫描页。"""

    def __init__(self) -> None:
        # 创建 OCR 引擎，后面处理各页时重复使用这个对象。
        self._engine = RapidOCR()

    # 这个函数负责渲染并识别指定的 PDF 页面。
    def extract_pages(
        self,
        *,
        content: bytes,
        page_indices: tuple[int, ...],
    ) -> dict[int, str]:
        # 没有需要 OCR 的页面，就直接返回空字典。
        if not page_indices:
            return {}

        # 页数过多时提前拒绝，不让一个请求占用服务太久。
        if len(page_indices) > MAX_OCR_PAGES:
            raise KnowledgeFileParsingError(
                f"一份 PDF 最多对 {MAX_OCR_PAGES} 页执行 OCR"
            )

        try:
            # content 是上传文件的 bytes。
            # PdfDocument 把它变成可以按页读取的 PDF 对象。
            document = pdfium.PdfDocument(content)
        except Exception as error:
            raise KnowledgeFileParsingError(
                "扫描 PDF 无法渲染"
            ) from error

        # key 是从 0 开始的页号，value 是该页识别出的文字。
        results: dict[int, str] = {}

        try:
            # 只处理 PdfParser 指定的页面，不重新识别文字型页面。
            for page_index in page_indices:
                # 防止访问 PDF 中不存在的页。
                if page_index < 0 or page_index >= len(document):
                    raise KnowledgeFileParsingError(
                        "OCR 页号超出 PDF 范围"
                    )

                # 取出当前页。例如 document[0] 是第一页。
                page = document[page_index]

                try:
                    # 渲染前先检查页面大小。
                    width, height = page.get_size()

                    # 宽、高各放大 PDF_RENDER_SCALE 倍，
                    # 因此像素总量大约乘以它的平方。
                    rendered_pixels = (
                        width
                        * height
                        * PDF_RENDER_SCALE**2
                    )

                    if rendered_pixels > MAX_RENDERED_PAGE_PIXELS:
                        raise KnowledgeFileParsingError(
                            "PDF 页面过大，无法安全执行 OCR"
                        )

                    # PDF 页面 → 像素图。
                    bitmap = page.render(
                        scale=PDF_RENDER_SCALE
                    )

                    try:
                        # 像素图 → OCR 可以读取的图片对象。
                        image = bitmap.to_pil()

                        # 图片 → OCR 识别结果。
                        output = self._engine(image)

                        # 没有识别到文字时，用空元组代替 None，
                        # 这样下面仍然可以安全地遍历。
                        lines = (
                            output.txts
                            if output.txts is not None
                            else ()
                        )

                        # 去掉每行两端的空格，过滤空行，
                        # 再把这一页的文字用换行符连接。
                        results[page_index] = "\n".join(
                            line.strip()
                            for line in lines
                            if line.strip()
                        )
                    finally:
                        # OCR 已经用完图片，释放像素图资源。
                        bitmap.close()
                finally:
                    # 当前页处理完毕，释放页面资源。
                    page.close()

        except KnowledgeFileParsingError:
            # 页号越界、页面过大等明确的业务错误保持原样。
            raise
        except Exception as error:
            # 其他底层错误统一变成文件解析错误。
            # from error 保留原始原因，方便排查。
            raise KnowledgeFileParsingError(
                "扫描 PDF 的 OCR 处理失败"
            ) from error
        finally:
            # 无论成功还是失败，最后都关闭整份 PDF。
            document.close()

        # 例如：{0: "第一页文字", 2: "第三页文字"}。
        return results