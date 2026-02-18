"""IRS Form 8949 line item generation."""
from decimal import Decimal
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Wallet, Disposal


class Form8949Generator:
    def __init__(self, session: Session):
        self.session = session

    def generate(self, wallet_ids: list[int] | None = None, year: int = 2025) -> dict:
        """
        Generate Form 8949 line items split into short-term and long-term.
        Returns dict with 'short_term' and 'long_term' lists.
        """
        query = self.session.query(Disposal)
        if wallet_ids:
            query = query.filter(Disposal.wallet_id.in_(wallet_ids))
        query = query.filter(
            func.extract("year", Disposal.disposal_date) == year
        ).order_by(Disposal.disposal_date.asc())

        disposals = query.all()

        short_term = []
        long_term = []

        for d in disposals:
            lot = d.cost_basis_lot
            line = {
                "description": f"{float(d.disposal_amount):.6f} {d.token_symbol or 'Unknown'}",
                "date_acquired": lot.acquisition_date.strftime("%m/%d/%Y") if lot and lot.acquisition_date else "VARIOUS",
                "date_sold": d.disposal_date.strftime("%m/%d/%Y") if d.disposal_date else "",
                "proceeds": float(d.proceeds_usd or 0),
                "cost_basis": float(d.cost_basis_usd or 0),
                "code": "",
                "adjustment": 0.0,
                "gain_loss": float(d.gain_loss_usd or 0),
                "tx_signature": d.disposal_tx_signature,
                "box": "",
            }

            if d.holding_period == "short":
                line["box"] = "C"  # Box C: short-term, no 1099
                short_term.append(line)
            else:
                line["box"] = "F"  # Box F: long-term, no 1099
                long_term.append(line)

        # Totals
        st_totals = {
            "total_proceeds": sum(i["proceeds"] for i in short_term),
            "total_cost_basis": sum(i["cost_basis"] for i in short_term),
            "total_gain_loss": sum(i["gain_loss"] for i in short_term),
            "count": len(short_term),
        }
        lt_totals = {
            "total_proceeds": sum(i["proceeds"] for i in long_term),
            "total_cost_basis": sum(i["cost_basis"] for i in long_term),
            "total_gain_loss": sum(i["gain_loss"] for i in long_term),
            "count": len(long_term),
        }

        return {
            "year": year,
            "short_term": short_term,
            "long_term": long_term,
            "short_term_totals": st_totals,
            "long_term_totals": lt_totals,
        }
