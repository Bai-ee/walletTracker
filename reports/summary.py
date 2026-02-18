"""Comprehensive summary report generation."""
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    Wallet, Transaction, TokenTransfer, SolTransfer, Swap,
    CostBasisLot, Disposal, WalletInteraction
)
from processors.pnl_calculator import PnLCalculator
from config import STABLECOIN_MINTS


class SummaryReporter:
    def __init__(self, session: Session, pnl_calculator: PnLCalculator | None = None):
        self.session = session
        self.pnl = pnl_calculator or PnLCalculator(session)

    def wallet_overview(self, wallet: Wallet) -> dict:
        """Generate a complete wallet overview."""
        tx_count = (
            self.session.query(func.count(Transaction.id))
            .filter(Transaction.wallet_id == wallet.id)
            .scalar()
        )

        # Transaction type breakdown
        type_counts = (
            self.session.query(Transaction.tx_type, func.count(Transaction.id))
            .filter(Transaction.wallet_id == wallet.id)
            .group_by(Transaction.tx_type)
            .all()
        )

        # Total SOL in/out
        sol_in = (
            self.session.query(func.coalesce(func.sum(SolTransfer.amount_sol), 0))
            .filter(SolTransfer.wallet_id == wallet.id, SolTransfer.direction == "in")
            .scalar()
        )
        sol_out = (
            self.session.query(func.coalesce(func.sum(SolTransfer.amount_sol), 0))
            .filter(SolTransfer.wallet_id == wallet.id, SolTransfer.direction == "out")
            .scalar()
        )

        # Unique tokens interacted with
        unique_tokens = (
            self.session.query(func.count(func.distinct(TokenTransfer.mint_address)))
            .filter(TokenTransfer.wallet_id == wallet.id)
            .scalar()
        )

        # Swap count
        swap_count = (
            self.session.query(func.count(Swap.id))
            .filter(Swap.wallet_id == wallet.id)
            .scalar()
        )

        return {
            "address": wallet.address,
            "label": wallet.label,
            "first_seen": wallet.first_seen.isoformat() if wallet.first_seen else None,
            "last_scanned": wallet.last_scanned.isoformat() if wallet.last_scanned else None,
            "total_transactions": tx_count,
            "type_breakdown": dict(type_counts),
            "total_sol_in": float(sol_in),
            "total_sol_out": float(sol_out),
            "net_sol_flow": float(Decimal(str(sol_in)) - Decimal(str(sol_out))),
            "unique_tokens": unique_tokens,
            "total_swaps": swap_count,
        }

    def global_summary(self, year: int | None = None) -> dict:
        """Generate a global summary across all wallets."""
        wallets = self.session.query(Wallet).all()

        total_pnl = {
            "short_term_gains": 0.0,
            "short_term_losses": 0.0,
            "long_term_gains": 0.0,
            "long_term_losses": 0.0,
            "total_income": 0.0,
            "total_fees": 0.0,
            "net_realized_pnl": 0.0,
        }

        wallet_summaries = []
        for w in wallets:
            pnl = self.pnl.wallet_summary(w, year=year)
            wallet_summaries.append({
                "address": w.address,
                "label": w.label,
                **pnl,
            })
            for key in total_pnl:
                if key in pnl:
                    total_pnl[key] += pnl[key]

        # Flagged items
        unknown_count = (
            self.session.query(func.count(Transaction.id))
            .filter(Transaction.tx_type == "UNKNOWN")
            .scalar()
        )
        missing_price_count = (
            self.session.query(func.count(TokenTransfer.id))
            .filter(TokenTransfer.usd_value_at_time.is_(None))
            .scalar()
        )

        return {
            "year": year,
            "wallet_count": len(wallets),
            "wallets": wallet_summaries,
            "totals": total_pnl,
            "flagged": {
                "unknown_transactions": unknown_count,
                "missing_prices": missing_price_count,
            },
        }

    def counterparty_summary(self, wallet: Wallet, limit: int = 20) -> list[dict]:
        """Get top counterparties for a wallet."""
        interactions = (
            self.session.query(WalletInteraction)
            .filter(WalletInteraction.wallet_id == wallet.id)
            .order_by(
                (WalletInteraction.total_received_usd + WalletInteraction.total_sent_usd).desc()
            )
            .limit(limit)
            .all()
        )

        return [
            {
                "address": i.counterparty_address,
                "label": i.counterparty_label,
                "total_received_sol": float(i.total_received_sol),
                "total_sent_sol": float(i.total_sent_sol),
                "total_received_usd": float(i.total_received_usd),
                "total_sent_usd": float(i.total_sent_usd),
                "token_transfers_in": i.token_transfers_in,
                "token_transfers_out": i.token_transfers_out,
                "tx_count": i.tx_count,
                "first_interaction": i.first_interaction.isoformat() if i.first_interaction else None,
                "last_interaction": i.last_interaction.isoformat() if i.last_interaction else None,
            }
            for i in interactions
        ]

    def token_holdings(self, wallet: Wallet) -> list[dict]:
        """Get current token holdings with cost basis info."""
        lots = (
            self.session.query(CostBasisLot)
            .filter(
                CostBasisLot.wallet_id == wallet.id,
                CostBasisLot.remaining_amount > 0,
            )
            .all()
        )

        # Aggregate by token
        holdings: dict[str, dict] = {}
        for lot in lots:
            mint = lot.mint_address
            if mint not in holdings:
                holdings[mint] = {
                    "mint": mint,
                    "symbol": lot.token_symbol or "???",
                    "total_amount": Decimal("0"),
                    "total_cost_basis": Decimal("0"),
                    "lots": 0,
                }
            h = holdings[mint]
            h["total_amount"] += lot.remaining_amount
            h["total_cost_basis"] += lot.cost_basis_per_unit_usd * lot.remaining_amount
            h["lots"] += 1

        result = []
        for h in holdings.values():
            avg_cost = h["total_cost_basis"] / h["total_amount"] if h["total_amount"] > 0 else Decimal("0")
            result.append({
                "mint": h["mint"],
                "symbol": h["symbol"],
                "amount": float(h["total_amount"]),
                "total_cost_basis_usd": float(h["total_cost_basis"]),
                "avg_cost_per_unit": float(avg_cost),
                "lot_count": h["lots"],
            })

        result.sort(key=lambda x: x["total_cost_basis_usd"], reverse=True)
        return result
