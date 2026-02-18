"""Gain/loss calculation and PnL reporting per wallet and per token."""
import logging
from decimal import Decimal
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    Wallet, Transaction, CostBasisLot, Disposal, TokenTransfer, SolTransfer
)
from fetchers.price_fetcher import PriceFetcher
from config import STABLECOIN_MINTS

logger = logging.getLogger(__name__)

SOL_MINT = "So11111111111111111111111111111111111111112"


class PnLCalculator:
    def __init__(self, session: Session, price_fetcher: PriceFetcher | None = None):
        self.session = session
        self.price_fetcher = price_fetcher

    def wallet_summary(self, wallet: Wallet, year: int | None = None) -> dict:
        """Calculate comprehensive PnL summary for a wallet."""
        query = self.session.query(Disposal).filter(Disposal.wallet_id == wallet.id)
        if year:
            query = query.filter(
                func.extract("year", Disposal.disposal_date) == year
            )
        disposals = query.all()

        short_term_gains = Decimal("0")
        short_term_losses = Decimal("0")
        long_term_gains = Decimal("0")
        long_term_losses = Decimal("0")
        total_proceeds = Decimal("0")
        total_cost_basis = Decimal("0")

        for d in disposals:
            gl = d.gain_loss_usd or Decimal("0")
            total_proceeds += d.proceeds_usd or Decimal("0")
            total_cost_basis += d.cost_basis_usd or Decimal("0")

            if d.holding_period == "short":
                if gl >= 0:
                    short_term_gains += gl
                else:
                    short_term_losses += gl
            else:
                if gl >= 0:
                    long_term_gains += gl
                else:
                    long_term_losses += gl

        # Income (airdrops + staking rewards)
        income_types = ("STAKING_REWARD", "AIRDROP")
        income_query = (
            self.session.query(Transaction)
            .filter(
                Transaction.wallet_id == wallet.id,
                Transaction.tx_type.in_(income_types),
            )
        )
        if year:
            income_query = income_query.filter(
                func.extract("year", Transaction.block_time) == year
            )
        income_txs = income_query.all()

        total_income = Decimal("0")
        for tx in income_txs:
            # Sum up all inbound values for income transactions
            sol_in = (
                self.session.query(func.coalesce(func.sum(SolTransfer.usd_value_at_time), 0))
                .filter(SolTransfer.transaction_id == tx.id, SolTransfer.direction == "in")
                .scalar()
            )
            token_in = (
                self.session.query(func.coalesce(func.sum(TokenTransfer.usd_value_at_time), 0))
                .filter(TokenTransfer.transaction_id == tx.id, TokenTransfer.direction == "in")
                .scalar()
            )
            total_income += Decimal(str(sol_in)) + Decimal(str(token_in))

        # Total fees
        fee_query = (
            self.session.query(func.coalesce(func.sum(Transaction.fee_usd), 0))
            .filter(Transaction.wallet_id == wallet.id)
        )
        if year:
            fee_query = fee_query.filter(
                func.extract("year", Transaction.block_time) == year
            )
        total_fees = Decimal(str(fee_query.scalar()))

        # Unrealized PnL
        unrealized = self._unrealized_pnl(wallet)

        return {
            "short_term_gains": float(short_term_gains),
            "short_term_losses": float(short_term_losses),
            "long_term_gains": float(long_term_gains),
            "long_term_losses": float(long_term_losses),
            "net_short_term": float(short_term_gains + short_term_losses),
            "net_long_term": float(long_term_gains + long_term_losses),
            "net_realized_pnl": float(short_term_gains + short_term_losses + long_term_gains + long_term_losses),
            "total_proceeds": float(total_proceeds),
            "total_cost_basis": float(total_cost_basis),
            "total_income": float(total_income),
            "total_fees": float(total_fees),
            "unrealized_pnl": unrealized,
            "disposal_count": len(disposals),
        }

    def token_summary(self, wallet: Wallet, year: int | None = None) -> list[dict]:
        """Calculate per-token PnL summary."""
        # Get all unique mints from lots
        mints = (
            self.session.query(CostBasisLot.mint_address, CostBasisLot.token_symbol)
            .filter(CostBasisLot.wallet_id == wallet.id)
            .distinct()
            .all()
        )

        summaries = []
        for mint, symbol in mints:
            if mint in STABLECOIN_MINTS:
                continue

            disposals_q = self.session.query(Disposal).filter(
                Disposal.wallet_id == wallet.id,
                Disposal.mint_address == mint,
            )
            if year:
                disposals_q = disposals_q.filter(
                    func.extract("year", Disposal.disposal_date) == year
                )
            disposals = disposals_q.all()

            realized_pnl = sum(d.gain_loss_usd or Decimal("0") for d in disposals)
            total_proceeds = sum(d.proceeds_usd or Decimal("0") for d in disposals)
            total_cost = sum(d.cost_basis_usd or Decimal("0") for d in disposals)
            total_disposed = sum(d.disposal_amount or Decimal("0") for d in disposals)

            # Current holdings
            remaining_lots = (
                self.session.query(CostBasisLot)
                .filter(
                    CostBasisLot.wallet_id == wallet.id,
                    CostBasisLot.mint_address == mint,
                    CostBasisLot.remaining_amount > 0,
                )
                .all()
            )
            current_holding = sum(l.remaining_amount or Decimal("0") for l in remaining_lots)
            avg_cost = Decimal("0")
            if current_holding > 0:
                total_remaining_cost = sum(
                    l.cost_basis_per_unit_usd * l.remaining_amount
                    for l in remaining_lots
                )
                avg_cost = total_remaining_cost / current_holding

            summaries.append({
                "mint": mint,
                "symbol": symbol or "???",
                "current_holding": float(current_holding),
                "avg_cost_basis": float(avg_cost),
                "total_disposed": float(total_disposed),
                "realized_pnl": float(realized_pnl),
                "total_proceeds": float(total_proceeds),
                "total_cost_basis": float(total_cost),
                "disposal_count": len(disposals),
            })

        summaries.sort(key=lambda x: abs(x["realized_pnl"]), reverse=True)
        return summaries

    def _unrealized_pnl(self, wallet: Wallet) -> dict:
        """Calculate unrealized PnL for current holdings."""
        lots = (
            self.session.query(CostBasisLot)
            .filter(
                CostBasisLot.wallet_id == wallet.id,
                CostBasisLot.remaining_amount > 0,
            )
            .all()
        )

        total_cost = Decimal("0")
        total_current_value = Decimal("0")
        by_token = {}

        for lot in lots:
            holding_cost = lot.cost_basis_per_unit_usd * lot.remaining_amount
            total_cost += holding_cost

            # Get current price if price fetcher is available
            current_value = Decimal("0")
            if self.price_fetcher:
                from datetime import date
                price = self.price_fetcher.get_price(lot.mint_address, date.today())
                if price:
                    current_value = price * lot.remaining_amount

            total_current_value += current_value

            symbol = lot.token_symbol or lot.mint_address[:8]
            if symbol not in by_token:
                by_token[symbol] = {"cost": Decimal("0"), "value": Decimal("0"), "amount": Decimal("0")}
            by_token[symbol]["cost"] += holding_cost
            by_token[symbol]["value"] += current_value
            by_token[symbol]["amount"] += lot.remaining_amount

        return {
            "total_cost_basis": float(total_cost),
            "total_current_value": float(total_current_value),
            "unrealized_gain_loss": float(total_current_value - total_cost),
            "by_token": {
                k: {
                    "amount": float(v["amount"]),
                    "cost_basis": float(v["cost"]),
                    "current_value": float(v["value"]),
                    "unrealized_pnl": float(v["value"] - v["cost"]),
                }
                for k, v in by_token.items()
            },
        }
