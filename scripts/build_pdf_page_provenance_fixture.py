"""生成用于验证 PDF 多页来源追踪的合成测试文档。

运行：python scripts/build_pdf_page_provenance_fixture.py
输出：output/pdf/ticketpilot_page_provenance_test.pdf

文档中的规则和编号仅供测试，不代表真实企业制度。
"""

from io import BytesIO
from pathlib import Path
from textwrap import wrap

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "pdf"
    / "ticketpilot_page_provenance_test.pdf"
)

# 每一页都有独立主题和唯一编号，便于检查搜索结果是否标对页码。
PAGES = (
    (
        "1 / Account recovery",
        "RESET-31",
        "The reset code expires after 20 minutes.",
        "Password recovery is handled by the IT service desk through a tracked ticket. "
        "An employee starts the request in the company portal and provides a corporate email address. "
        "A technician records the request time, ticket number, and verification outcome before taking action.",
        "The account recovery reference is RESET-31. The reset code expires after 20 minutes. "
        "The technician sends the code only to the verified corporate mailbox and never through a public chat. "
        "The employee must choose a new password after opening the approved reset link.",
        "Identity verification uses the employee ID and the department recorded in the directory. "
        "A caller who cannot access the corporate mailbox is directed to the supervisor for a second check. "
        "The supervisor records the confirmation in the same ticket so that the audit trail is complete.",
        "The service desk does not ask for an existing password. The technician does not share a temporary "
        "password in a chat room, screenshot, or personal mailbox. Suspicious requests are escalated to the "
        "security team, and the original request remains open until the investigation is complete.",
        "After the reset, the employee signs in and enables multi-factor authentication. The service desk "
        "confirms the result in the ticket and closes it only after the employee acknowledges access. "
        "This document does not list any administrator telephone number or private contact details.",
    ),
    (
        "2 / Equipment repair",
        "REPAIR-52",
        "The repair warranty lasts 90 calendar days from delivery acceptance.",
        "Equipment repair requests use a separate workflow from account recovery. The requester records "
        "the device serial number, the reported symptom, and the current location. The maintenance team "
        "inspects the device and adds a diagnostic note before proposing a repair or replacement.",
        "A supervisor approves work that exceeds the standard service allowance. The approval is attached "
        "to the repair ticket before parts are ordered. The technician records the part number, installation "
        "date, and the person who accepted the repaired equipment.",
        "The equipment repair reference is REPAIR-52. The repair warranty lasts 90 calendar days from "
        "delivery acceptance. The repair date itself is not the start date. The acceptance record is the "
        "source of truth when calculating the final warranty day.",
        "Warranty coverage applies only to the repaired component under normal use. Damage caused by "
        "unauthorized modification is reviewed separately. The team checks the acceptance record and "
        "the service history before confirming whether a new request is covered.",
        "The requester can follow the repair status through the ticket portal. The service desk does not "
        "promise an exact delivery time before the maintenance team confirms parts availability. The final "
        "repair note includes the test result and the name of the accepting employee.",
    ),
    (
        "3 / Travel reimbursement",
        "TRAVEL-63",
        "Receipts are due 12 calendar days after the trip ends.",
        "Travel reimbursement is submitted in the finance portal, not in an equipment or password ticket. "
        "The employee supplies the approved itinerary, itemized receipts, and a short explanation of the "
        "business purpose. Finance checks the submitted dates against the travel approval.",
        "An expense without a readable receipt is returned for correction. A missing receipt is not replaced "
        "by a verbal statement. The employee may attach an explanatory note, but finance still applies the "
        "documented exception process before approving the claim.",
        "A manager reviews costs outside the approved itinerary. The manager's written decision stays "
        "with the reimbursement record. Finance does not change the expense category merely to make "
        "an otherwise ineligible claim pass the automatic review.",
        "The travel reimbursement reference is TRAVEL-63. Receipts are due 12 calendar days after the trip "
        "ends. A late submission needs a written explanation from the employee and approval from the manager. "
        "The deadline is measured in calendar days rather than working days.",
        "After approval, finance marks the claim as payable and records the payment reference. The employee "
        "can review the status in the portal. This synthetic policy does not specify a finance telephone "
        "number, a bank account, or an exception approval by text message.",
    ),
)


def build_pdf_bytes() -> bytes:
    """将三组测试内容写成真正的三页文字型 PDF。"""

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    for page_number, (heading, marker, answer, *paragraphs) in enumerate(
        PAGES, start=1
    ):
        pdf.setFillColor(colors.HexColor("#15324f"))
        pdf.rect(0, page_height - 92, page_width, 92, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 17)
        pdf.drawString(45, page_height - 48, "TicketPilot / PDF provenance test")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(45, page_height - 70, heading)

        pdf.setFillColor(colors.HexColor("#223344"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(45, page_height - 120, f"Unique marker: {marker}")
        pdf.drawString(45, page_height - 140, f"Expected answer: {answer}")

        # 每页的正文都超过默认 800 字符切块阈值。
        # 这样测试中既会出现单页片段，也会出现跨页片段。
        y = page_height - 175
        pdf.setFont("Helvetica", 9.5)
        for paragraph in paragraphs:
            for line in wrap(paragraph, width=95):
                pdf.drawString(45, y, line)
                y -= 14
            y -= 11
        if y < 55:
            raise ValueError(f"Page {page_number} has too much text")

        pdf.setStrokeColor(colors.HexColor("#cbd5e1"))
        pdf.line(45, 50, page_width - 45, 50)
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(colors.HexColor("#64748b"))
        pdf.drawString(45, 35, "Synthetic test content - not a real company policy")
        pdf.drawRightString(page_width - 45, 35, f"Page {page_number} of 3")
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def main() -> None:
    """保存测试 PDF，供 Swagger 手动上传。"""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(build_pdf_bytes())
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
