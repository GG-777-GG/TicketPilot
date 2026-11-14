"""用真实三页 PDF bytes 验证解析、清洗、切块的页码传递。"""

from tests.factories.pdf import build_pdf
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
    RawDocument,
    SourcePage,
)
from ticketpilot.knowledge.domain.pipeline import prepare_document


# 自动化测试复用项目已有的 PDF 工厂，不依赖生成手动测试文件的 reportlab。
PAGE_CASES = (
    (
        "RESET-31",
        "RESET-31: The password reset code expires after 20 minutes. "
        "The service desk verifies the employee and records every action in a ticket. "
        "The code is sent only to the verified corporate mailbox.",
    ),
    (
        "REPAIR-52",
        "REPAIR-52: The equipment repair warranty lasts 90 calendar days from "
        "delivery acceptance. The repair date is not the start date. The service "
        "team records the device serial number and acceptance evidence.",
    ),
    (
        "TRAVEL-63",
        "TRAVEL-63: Expense receipts are due 12 calendar days after the trip ends. "
        "A late claim requires the employee explanation and written manager approval. "
        "The deadline is measured in calendar days, not working days.",
    ),
)


def test_real_three_page_pdf_preserves_source_pages_and_chunk_pages() -> None:
    # 使用与上传接口相同的服务解析真正的 PDF bytes。
    parsed = KnowledgeFileUploadService().parse_upload(
        filename="ticketpilot_page_provenance_test.pdf",
        content_type="application/pdf",
        content=build_pdf(*(page_text for _, page_text in PAGE_CASES)),
    )

    # 先检查 PDF 解析器是否按原始页序保留了三页及各页独有的编号。
    assert len(parsed.pages) == 3
    for page_number, (marker, _) in enumerate(PAGE_CASES, start=1):
        assert parsed.pages[page_number - 1].page_number == page_number
        assert marker in parsed.pages[page_number - 1].text
        assert all(
            marker not in page.text
            for other_page_number, page in enumerate(parsed.pages, start=1)
            if other_page_number != page_number
        )

    # 按正式入库的输入形式构造 RawDocument，再运行清洗和切块。
    raw = RawDocument(
        title="PDF 多页来源测试",
        content=parsed.text,
        source_type=KnowledgeSourceType.FILE,
        source_uri="ticketpilot_page_provenance_test.pdf",
        source_pages=tuple(
            SourcePage(page_number=page.page_number, text=page.text)
            for page in parsed.pages
        ),
    )
    # 用较短的切块长度稳定覆盖单页与跨页两种边界。
    prepared = prepare_document(raw, max_characters=100, overlap_characters=20)

    # 唯一编号所在片段必须包含真实来源页，不能把第 3 页写成第 1 页。
    for page_number, (marker, _) in enumerate(PAGE_CASES, start=1):
        matching_chunks = [
            chunk for chunk in prepared.chunks if marker in chunk.content
        ]
        assert matching_chunks
        assert all(page_number in chunk.page_numbers for chunk in matching_chunks)

    # 既要覆盖单页片段，也要覆盖相邻页交界处的跨页片段。
    page_sets = {chunk.page_numbers for chunk in prepared.chunks}
    assert (1,) in page_sets
    assert (2,) in page_sets
    assert (3,) in page_sets
    assert any(len(page_numbers) > 1 for page_numbers in page_sets)
