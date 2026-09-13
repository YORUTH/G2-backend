"""
Server-side PDF generation for finance reports.

Mirrors the layout of the frontend generatePDF.ts:
  - Red header banner with gym name, report title, and date range
  - Revenue Summary table
  - Transactions table with all member rows
  - Footer on every page
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Spacer,
    Paragraph,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER


# ── Colour palette (matching frontend) ──────────────────────────────────
RED = colors.Color(217 / 255, 10 / 255, 20 / 255)
DARK_GRAY = colors.Color(40 / 255, 40 / 255, 40 / 255)
LIGHT_BG = colors.Color(248 / 255, 248 / 255, 248 / 255)
HIGHLIGHT_BG = colors.Color(254 / 255, 240 / 255, 240 / 255)
TABLE_HEAD_BG = colors.Color(55 / 255, 65 / 255, 81 / 255)
WHITE = colors.white


def _fmt(n):
    """Format number as 'X,XXX Birr'."""
    try:
        return f"{int(n):,} Birr"
    except (TypeError, ValueError):
        return f"{n} Birr"


def _build_header_table(page_width, from_date_str, to_date_str):
    """Red banner header matching the frontend PDF layout."""
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "HeaderTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=WHITE,
        leading=20,
    )
    subtitle_style = ParagraphStyle(
        "HeaderSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=WHITE,
        leading=12,
    )
    right_style = ParagraphStyle(
        "HeaderRight",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        textColor=WHITE,
        alignment=TA_RIGHT,
        leading=10,
    )

    is_today = from_date_str == to_date_str
    if is_today:
        try:
            dt = datetime.strptime(from_date_str, "%Y-%m-%d")
            date_label = dt.strftime("%A, %B %d, %Y")
        except ValueError:
            date_label = from_date_str
    else:
        date_label = f"{from_date_str}  to  {to_date_str}"

    left_content = Paragraph("Jitu Fitness", title_style)
    mid_content = Paragraph(f"Finance Report<br/>{date_label}", subtitle_style)
    right_content = Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", right_style
    )

    tbl = Table(
        [[left_content, mid_content, right_content]],
        colWidths=[page_width * 0.28, page_width * 0.44, page_width * 0.28],
        rowHeights=[22 * mm],
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), RED),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (-1, -1), (-1, -1), 10),
            ]
        )
    )
    return tbl


def _build_summary_table(summary, page_width):
    """Revenue summary table matching the frontend layout."""
    styles = getSampleStyleSheet()
    label_style = ParagraphStyle(
        "SumLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=DARK_GRAY,
    )
    val_style = ParagraphStyle(
        "SumVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=DARK_GRAY,
        alignment=TA_RIGHT,
    )
    head_style = ParagraphStyle(
        "SumHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=WHITE,
    )
    head_val_style = ParagraphStyle(
        "SumHeadVal",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=WHITE,
        alignment=TA_RIGHT,
    )

    temporal = summary.get("temporal_total", 0) or 0
    boxing = summary.get("boxing_fees_total", 0) or 0

    data = [
        [Paragraph("Category", head_style), Paragraph("Amount", head_val_style)],
        [
            Paragraph("Grand Total", label_style),
            Paragraph(_fmt(summary.get("grand_total", 0)), val_style),
        ],
        [
            Paragraph("Active Members Total", label_style),
            Paragraph(_fmt(summary.get("active_total", 0)), val_style),
        ],
        [
            Paragraph("Card Fees Total", label_style),
            Paragraph(_fmt(summary.get("card_fees_total", 0)), val_style),
        ],
        [
            Paragraph("Other Fees (Temp+Box.)", label_style),
            Paragraph(_fmt(temporal + boxing), val_style),
        ],
        [
            Paragraph("Pending Total", label_style),
            Paragraph(_fmt(summary.get("pending_total", 0)), val_style),
        ],
    ]

    col_w = page_width * 0.45
    tbl = Table(data, colWidths=[col_w, col_w * 0.75])
    tbl.setStyle(
        TableStyle(
            [
                # Header row
                ("BACKGROUND", (0, 0), (-1, 0), RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                # Grand Total row highlight
                ("BACKGROUND", (0, 1), (-1, 1), HIGHLIGHT_BG),
                # Alternating rows
                ("BACKGROUND", (0, 3), (-1, 3), LIGHT_BG),
                ("BACKGROUND", (0, 5), (-1, 5), LIGHT_BG),
                # Grid
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.85, 0.85, 0.85)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return tbl


def _build_transactions_table(rows, page_width):
    """Transactions detail table matching the frontend layout."""
    styles = getSampleStyleSheet()
    head_style = ParagraphStyle(
        "THead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        textColor=WHITE,
        leading=9,
    )
    cell_style = ParagraphStyle(
        "TCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        textColor=DARK_GRAY,
        leading=9,
    )
    cell_right = ParagraphStyle(
        "TCellR",
        parent=cell_style,
        alignment=TA_RIGHT,
    )
    cell_bold = ParagraphStyle(
        "TCellB",
        parent=cell_style,
        fontName="Helvetica-Bold",
        alignment=TA_RIGHT,
    )
    cell_center = ParagraphStyle(
        "TCellC",
        parent=cell_style,
        alignment=TA_CENTER,
    )

    headers = ["#", "Name", "Status", "Package", "Mo.", "Base", "Card Fee", "Boxing", "Total", "Date"]
    header_row = [Paragraph(h, head_style) for h in headers]

    body_rows = []
    for i, r in enumerate(rows):
        paid_at = r.get("paid_at", "")
        if hasattr(paid_at, "strftime"):
            date_str = paid_at.strftime("%Y-%m-%d")
        else:
            try:
                date_str = datetime.fromisoformat(str(paid_at)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = str(paid_at)

        status_raw = r.get("status", "")
        pkg_raw = r.get("package_type", "")

        body_rows.append([
            Paragraph(str(i + 1), cell_center),
            Paragraph(f"{r.get('first_name', '')} {r.get('last_name', '')}", cell_style),
            Paragraph(status_raw.capitalize(), cell_style),
            Paragraph(pkg_raw.capitalize(), cell_style),
            Paragraph(str(r.get("months", 0)), cell_center),
            Paragraph(f"{r.get('base_amount', 0):,}", cell_right),
            Paragraph(f"{r.get('card_fee', 0):,}", cell_right),
            Paragraph(f"{r.get('boxing_fee', 0):,}", cell_right),
            Paragraph(_fmt(r.get("total_paid", 0)), cell_bold),
            Paragraph(date_str, cell_style),
        ])

    data = [header_row] + body_rows

    col_widths = [
        page_width * 0.04,  # #
        page_width * 0.16,  # Name
        page_width * 0.09,  # Status
        page_width * 0.10,  # Package
        page_width * 0.05,  # Months
        page_width * 0.10,  # Base
        page_width * 0.10,  # Card Fee
        page_width * 0.09,  # Boxing
        page_width * 0.14,  # Total
        page_width * 0.13,  # Date
    ]

    tbl = Table(data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        # Grid
        ("GRID", (0, 0), (-1, -1), 0.3, colors.Color(0.88, 0.88, 0.88)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]

    # Striped rows
    for row_idx in range(1, len(data)):
        if row_idx % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), LIGHT_BG))

    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _footer(canvas, doc):
    """Draw footer on each page."""
    canvas.saveState()
    page_width = A4[0]
    y = 12 * mm

    # Separator line
    canvas.setStrokeColor(colors.Color(0.86, 0.86, 0.86))
    canvas.line(14 * mm, y + 4 * mm, page_width - 14 * mm, y + 4 * mm)

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.Color(0.59, 0.59, 0.59))
    canvas.drawString(14 * mm, y, "Jitu Fitness - Confidential Finance Report")
    canvas.drawRightString(
        page_width - 14 * mm, y, f"Page {doc.page}"
    )
    canvas.restoreState()


def generate_finance_pdf(rows, summary, from_date_str, to_date_str):
    """
    Generate a finance report PDF and return it as a BytesIO buffer.

    Args:
        rows: list of transaction dicts from get_finance_report_data()
        summary: summary dict from get_finance_report_data()
        from_date_str: start date as 'YYYY-MM-DD'
        to_date_str: end date as 'YYYY-MM-DD'

    Returns:
        io.BytesIO containing the PDF data (seek(0) already called)
    """
    buf = io.BytesIO()
    page_width, page_height = A4
    content_width = page_width - 28 * mm  # 14mm margins on each side

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=10 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    section_title = ParagraphStyle(
        "SectionTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=DARK_GRAY,
        spaceAfter=4 * mm,
    )

    elements = []

    # ── Header banner ────────────────────────────────────────────────────
    elements.append(_build_header_table(content_width, from_date_str, to_date_str))
    elements.append(Spacer(1, 8 * mm))

    # ── Revenue Summary ──────────────────────────────────────────────────
    elements.append(Paragraph("Revenue Summary", section_title))
    elements.append(_build_summary_table(summary, content_width))
    elements.append(Spacer(1, 8 * mm))

    # ── Transactions ─────────────────────────────────────────────────────
    elements.append(
        Paragraph(f"Transactions ({len(rows)})", section_title)
    )
    if rows:
        elements.append(_build_transactions_table(rows, content_width))
    else:
        no_data_style = ParagraphStyle(
            "NoData",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=DARK_GRAY,
            alignment=TA_CENTER,
        )
        elements.append(Paragraph("No transactions found for this period.", no_data_style))

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    buf.seek(0)
    return buf
