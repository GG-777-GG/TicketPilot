"""在内存中生成 DOCX，供解析器测试使用。"""

from io import BytesIO

from docx import Document


def build_docx(
    *blocks: str | list[list[str]],
) -> bytes:
    """按传入顺序生成段落或表格，并返回 DOCX 的 bytes。"""

    document = Document()

    for block in blocks:
        if isinstance(block, str):
            document.add_paragraph(block)
            continue

        table = document.add_table(
            rows=len(block),
            cols=len(block[0]),
        )
        for row_index, row in enumerate(block):
            for cell_index, value in enumerate(row):
                table.cell(row_index, cell_index).text = value

    output = BytesIO()
    document.save(output)
    return output.getvalue()
