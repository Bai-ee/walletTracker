"""CSV export for tax reports and transaction data."""
import csv
import io
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Wallet, Transaction, TokenTransfer, SolTransfer, Swap, Disposal
from reports.form_8949 import Form8949Generator


class CSVExporter:
    def __init__(self, session: Session):
        self.session = session

    def export_form_8949(self, wallet_ids: list[int] | None = None, year: int = 2025) -> str:
        """Export Form 8949 compatible CSV."""
        gen = Form8949Generator(self.session)
        data = gen.generate(wallet_ids=wallet_ids, year=year)

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            "Description of Property",
            "Date Acquired",
            "Date Sold or Disposed Of",
            "Proceeds",
            "Cost or Other Basis",
            "Code",
            "Amount of Adjustment",
            "Gain or (Loss)",
            "Box",
            "Term",
        ])

        # Short-term
        for item in data["short_term"]:
            writer.writerow([
                item["description"],
                item["date_acquired"],
                item["date_sold"],
                f"{item['proceeds']:.2f}",
                f"{item['cost_basis']:.2f}",
                item["code"],
                f"{item['adjustment']:.2f}",
                f"{item['gain_loss']:.2f}",
                item["box"],
                "Short-term",
            ])

        # Long-term
        for item in data["long_term"]:
            writer.writerow([
                item["description"],
                item["date_acquired"],
                item["date_sold"],
                f"{item['proceeds']:.2f}",
                f"{item['cost_basis']:.2f}",
                item["code"],
                f"{item['adjustment']:.2f}",
                f"{item['gain_loss']:.2f}",
                item["box"],
                "Long-term",
            ])

        return output.getvalue()

    def export_turbotax(self, wallet_ids: list[int] | None = None, year: int = 2025) -> str:
        """Export TurboTax compatible CSV."""
        gen = Form8949Generator(self.session)
        data = gen.generate(wallet_ids=wallet_ids, year=year)

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Currency Name",
            "Purchase Date",
            "Cost Basis",
            "Date Sold",
            "Proceeds",
        ])

        all_items = data["short_term"] + data["long_term"]
        for item in all_items:
            writer.writerow([
                item["description"],
                item["date_acquired"],
                f"{item['cost_basis']:.2f}",
                item["date_sold"],
                f"{item['proceeds']:.2f}",
            ])

        return output.getvalue()

    def export_raw_transactions(self, wallet_id: int) -> str:
        """Export all raw transaction data as CSV."""
        transactions = (
            self.session.query(Transaction)
            .filter(Transaction.wallet_id == wallet_id)
            .order_by(Transaction.block_time.asc())
            .all()
        )

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Signature", "Date", "Type", "Fee (SOL)", "Fee (USD)", "Success",
            "SOL In", "SOL Out", "Token In", "Token In Symbol",
            "Token Out", "Token Out Symbol", "Swap From", "Swap To",
        ])

        for tx in transactions:
            sol_in = sum(
                float(s.amount_sol) for s in tx.sol_transfers if s.direction == "in"
            )
            sol_out = sum(
                float(s.amount_sol) for s in tx.sol_transfers if s.direction == "out"
            )

            token_in = ""
            token_in_sym = ""
            token_out = ""
            token_out_sym = ""
            for tt in tx.token_transfers:
                if tt.direction == "in":
                    token_in = str(float(tt.amount))
                    token_in_sym = tt.token_symbol or ""
                elif tt.direction == "out":
                    token_out = str(float(tt.amount))
                    token_out_sym = tt.token_symbol or ""

            swap_from = ""
            swap_to = ""
            for s in tx.swaps:
                swap_from = f"{float(s.from_amount)} {s.from_symbol or ''}"
                swap_to = f"{float(s.to_amount)} {s.to_symbol or ''}"

            writer.writerow([
                tx.signature,
                tx.block_time.strftime("%Y-%m-%d %H:%M:%S") if tx.block_time else "",
                tx.tx_type,
                f"{float(tx.fee_sol):.9f}" if tx.fee_sol else "0",
                f"{float(tx.fee_usd):.2f}" if tx.fee_usd else "0",
                tx.success,
                f"{sol_in:.9f}" if sol_in else "",
                f"{sol_out:.9f}" if sol_out else "",
                token_in,
                token_in_sym,
                token_out,
                token_out_sym,
                swap_from,
                swap_to,
            ])

        return output.getvalue()
