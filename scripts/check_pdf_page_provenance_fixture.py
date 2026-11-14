"""离线检查三页测试 PDF 的真实解析和页码映射。

运行：uv run python -m scripts.check_pdf_page_provenance_fixture
不连接数据库，也不调用 Ollama。
"""

from pathlib import Path

from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
    RawDocument,
    SourcePage,
)
from ticketpilot.knowledge.domain.pipeline import prepare_document


OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "pdf"
    / "ticketpilot_page_provenance_test.pdf"
)
EXPECTED_MARKERS = ("RESET-31", "REPAIR-52", "TRAVEL-63")


def main() -> None:
    """解析实际 PDF 文件并检查单页、跨页片段。"""

    # 解析实际交给用户手动上传的文件，而不是仅测试一份内存样本。
    parsed = KnowledgeFileUploadService().parse_upload(
        filename=OUTPUT_PATH.name,
        content_type="application/pdf",
        content=OUTPUT_PATH.read_bytes(),
    )
    assert len(parsed.pages) == 3, "测试 PDF 必须恰好有三页"

    # 将解析结果转换成正式预处理流程使用的文档对象。
    raw = RawDocument(
        title="PDF 多页来源测试",
        content=parsed.text,
        source_type=KnowledgeSourceType.FILE,
        source_uri=OUTPUT_PATH.name,
        source_pages=tuple(
            SourcePage(page_number=page.page_number, text=page.text)
            for page in parsed.pages
        ),
    )
    prepared = prepare_document(raw)

    # 每个唯一编号必须出现在自己的原始页和对应的知识片段中。
    for expected_page, marker in enumerate(EXPECTED_MARKERS, start=1):
        assert marker in parsed.pages[expected_page - 1].text
        matching_chunks = [
            chunk for chunk in prepared.chunks if marker in chunk.content
        ]
        assert matching_chunks, f"没有找到编号 {marker} 的片段"
        assert all(
            expected_page in chunk.page_numbers for chunk in matching_chunks
        ), f"编号 {marker} 的片段页码错误"

    # 检查至少有单页片段和跨页片段，覆盖最重要的两种情况。
    page_sets = {chunk.page_numbers for chunk in prepared.chunks}
    assert {(1,), (2,), (3,)} <= page_sets
    assert any(len(page_numbers) > 1 for page_numbers in page_sets)

    print(f"PDF 页数: {len(parsed.pages)}")
    print(f"片段数量: {prepared.chunk_count}")
    for chunk in prepared.chunks:
        print(f"片段 {chunk.chunk_index}: 页码 {list(chunk.page_numbers)}")
    print("页码映射检查通过")


if __name__ == "__main__":
    main()
