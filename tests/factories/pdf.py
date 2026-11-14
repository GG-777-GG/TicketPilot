"""在内存中生成 PDF，供解析器和上传接口测试使用。"""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)


def build_pdf(
    *page_texts: str,
    password: str | None = None,
) -> bytes:
    """生成包含简单英文文字的 PDF，不依赖磁盘文件。"""

    writer = PdfWriter()

    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)

        if text:
            # PDF 内置字体足够测试英文文字提取，不需要额外字体文件。
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {
                            NameObject("/F1"): DictionaryObject(
                                {
                                    NameObject("/Type"): NameObject("/Font"),
                                    NameObject("/Subtype"): NameObject("/Type1"),
                                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                                }
                            ),
                        }
                    ),
                }
            )
            stream = DecodedStreamObject()
            stream.set_data(
                b"BT /F1 12 Tf 72 720 Td ("
                + text.encode("ascii")
                + b") Tj ET"
            )
            page.replace_contents(stream)

    if password is not None:
        writer.encrypt(password)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
