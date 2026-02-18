"""PDF summary report generation using ReportLab."""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

from sqlalchemy.orm import Session
from database.models import Wallet
from reports.summary import SummaryReporter
from reports.form_8949 import Form8949Generator


class PDFReportGenerator:
    def __init__(self, session: Session):
        self.session = session
        self.summary = SummaryReporter(session)
        self.form_gen = Form8949Generator(session)

    def generate(self, year: int = 2025, wallet_ids: list[int] | None = None) -> bytes:
        """Generate a PDF tax summary report. Returns PDF bytes."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=18, spaceAfter=12)
        heading_style = ParagraphStyle("Heading", parent=styles["Heading2"], fontSize=14, spaceAfter=8)
        normal = styles["Normal"]

        elements = []

        # Title page
        elements.append(Paragraph(f"Solana Tax Report — {year}", title_style))
        elements.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", normal))
        elements.append(Spacer(1, 0.3 * inch))

        # Global summary
        global_data = self.summary.global_summary(year=year)
        totals = global_data["totals"]

        elements.append(Paragraph("Tax Summary", heading_style))
        summary_data = [
            ["Category", "Amount (USD)"],
            ["Short-Term Gains", f"${totals['short_term_gains']:,.2f}"],
            ["Short-Term Losses", f"(${abs(totals['short_term_losses']):,.2f})"],
            ["Long-Term Gains", f"${totals['long_term_gains']:,.2f}"],
            ["Long-Term Losses", f"(${abs(totals['long_term_losses']):,.2f})"],
            ["Net Realized P&L", f"${totals['net_realized_pnl']:,.2f}"],
            ["Total Income", f"${totals['total_income']:,.2f}"],
            ["Total Fees", f"${totals['total_fees']:,.2f}"],
        ]
        t = Table(summary_data, colWidths=[3 * inch, 2.5 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.3 * inch))

        # Per-wallet summaries
        for ws in global_data["wallets"]:
            label = ws.get("label") or ws["address"][:12] + "..."
            elements.append(Paragraph(f"Wallet: {label}", heading_style))
            wallet_data = [
                ["Metric", "Value"],
                ["Net Short-Term", f"${ws['net_short_term']:,.2f}"],
                ["Net Long-Term", f"${ws['net_long_term']:,.2f}"],
                ["Total Income", f"${ws['total_income']:,.2f}"],
                ["Disposal Count", str(ws["disposal_count"])],
            ]
            t = Table(wallet_data, colWidths=[3 * inch, 2.5 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            elements.append(t)
            elements.append(Spacer(1, 0.2 * inch))

        # Form 8949 detail
        elements.append(PageBreak())
        elements.append(Paragraph("Form 8949 — Short-Term (Box C)", heading_style))

        form_data = self.form_gen.generate(wallet_ids=wallet_ids, year=year)

        if form_data["short_term"]:
            st_rows = [["Description", "Acquired", "Sold", "Proceeds", "Cost Basis", "Gain/Loss"]]
            for item in form_data["short_term"][:100]:  # Limit rows for PDF
                st_rows.append([
                    item["description"][:30],
                    item["date_acquired"],
                    item["date_sold"],
                    f"${item['proceeds']:,.2f}",
                    f"${item['cost_basis']:,.2f}",
                    f"${item['gain_loss']:,.2f}",
                ])
            st_rows.append([
                "TOTALS", "", "",
                f"${form_data['short_term_totals']['total_proceeds']:,.2f}",
                f"${form_data['short_term_totals']['total_cost_basis']:,.2f}",
                f"${form_data['short_term_totals']['total_gain_loss']:,.2f}",
            ])

            t = Table(st_rows, colWidths=[1.8 * inch, 0.9 * inch, 0.9 * inch, 1 * inch, 1 * inch, 1 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            elements.append(t)
        else:
            elements.append(Paragraph("No short-term disposals.", normal))

        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph("Form 8949 — Long-Term (Box F)", heading_style))

        if form_data["long_term"]:
            lt_rows = [["Description", "Acquired", "Sold", "Proceeds", "Cost Basis", "Gain/Loss"]]
            for item in form_data["long_term"][:100]:
                lt_rows.append([
                    item["description"][:30],
                    item["date_acquired"],
                    item["date_sold"],
                    f"${item['proceeds']:,.2f}",
                    f"${item['cost_basis']:,.2f}",
                    f"${item['gain_loss']:,.2f}",
                ])
            lt_rows.append([
                "TOTALS", "", "",
                f"${form_data['long_term_totals']['total_proceeds']:,.2f}",
                f"${form_data['long_term_totals']['total_cost_basis']:,.2f}",
                f"${form_data['long_term_totals']['total_gain_loss']:,.2f}",
            ])

            t = Table(lt_rows, colWidths=[1.8 * inch, 0.9 * inch, 0.9 * inch, 1 * inch, 1 * inch, 1 * inch])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            elements.append(t)
        else:
            elements.append(Paragraph("No long-term disposals.", normal))

        # Flagged items
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph("Flagged Items", heading_style))
        flagged = global_data["flagged"]
        elements.append(Paragraph(f"Unknown transaction types: {flagged['unknown_transactions']}", normal))
        elements.append(Paragraph(f"Transfers missing USD prices: {flagged['missing_prices']}", normal))

        doc.build(elements)
        return buffer.getvalue()
