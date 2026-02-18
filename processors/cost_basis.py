"""FIFO/LIFO cost basis engine for tracking token acquisitions and disposals."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from database.models import (
    Wallet, Transaction, Swap, TokenTransfer, SolTransfer,
    CostBasisLot, Disposal
)
from config import STABLECOIN_MINTS, DUST_THRESHOLD_USD, LONG_TERM_HOLDING_DAYS

logger = logging.getLogger(__name__)

SOL_MINT = "So11111111111111111111111111111111111111112"


class CostBasisEngine:
    def __init__(self, session: Session, method: str = "fifo"):
        self.session = session
        self.method = method  # "fifo" or "lifo"

    def process_wallet(self, wallet: Wallet, tracked_wallets: set[str] | None = None) -> dict:
        """
        Process all transactions for a wallet and build cost basis lots + disposals.
        Returns summary stats.
        """
        tracked = tracked_wallets or set()

        # Clear existing lots and disposals for reprocessing
        self.session.query(Disposal).filter(Disposal.wallet_id == wallet.id).delete()
        self.session.query(CostBasisLot).filter(CostBasisLot.wallet_id == wallet.id).delete()
        self.session.flush()

        # Get all transactions ordered by time
        transactions = (
            self.session.query(Transaction)
            .filter(Transaction.wallet_id == wallet.id)
            .order_by(Transaction.block_time.asc())
            .all()
        )

        stats = {"lots_created": 0, "disposals_created": 0, "errors": 0}

        for tx in transactions:
            try:
                self._process_transaction(wallet, tx, tracked, stats)
            except Exception as e:
                logger.error(f"Error processing cost basis for tx {tx.signature}: {e}")
                stats["errors"] += 1

        self.session.commit()
        logger.info(
            f"Cost basis for {wallet.address[:8]}...: "
            f"{stats['lots_created']} lots, {stats['disposals_created']} disposals, "
            f"{stats['errors']} errors"
        )
        return stats

    def _process_transaction(self, wallet: Wallet, tx: Transaction, tracked: set[str], stats: dict):
        """Process a single transaction for cost basis."""
        tx_type = tx.tx_type

        # Handle swaps
        swaps = self.session.query(Swap).filter(Swap.transaction_id == tx.id).all()
        for swap in swaps:
            self._process_swap(wallet, tx, swap, stats)
            return

        # Handle SOL transfers
        sol_transfers = self.session.query(SolTransfer).filter(SolTransfer.transaction_id == tx.id).all()
        for st in sol_transfers:
            self._process_sol_transfer(wallet, tx, st, tracked, tx_type, stats)

        # Handle token transfers
        token_transfers = self.session.query(TokenTransfer).filter(TokenTransfer.transaction_id == tx.id).all()
        for tt in token_transfers:
            self._process_token_transfer(wallet, tx, tt, tracked, tx_type, stats)

    def _process_swap(self, wallet: Wallet, tx: Transaction, swap: Swap, stats: dict):
        """Process a swap: dispose of from_token, acquire to_token."""
        # Skip stablecoin-to-stablecoin
        if swap.from_mint in STABLECOIN_MINTS and swap.to_mint in STABLECOIN_MINTS:
            return

        # Dispose of from_token (unless it's the base currency in a buy)
        if swap.from_mint and swap.from_amount and swap.from_amount > 0:
            if swap.from_mint not in STABLECOIN_MINTS:
                proceeds = swap.from_usd_value or Decimal("0")
                self._create_disposal(
                    wallet, swap.from_mint, swap.from_symbol,
                    swap.from_amount, proceeds, tx, "swap_out", stats
                )
            # For buys with SOL/stablecoin, still need to dispose of the SOL
            elif swap.from_mint == SOL_MINT:
                proceeds = swap.from_usd_value or Decimal("0")
                self._create_disposal(
                    wallet, SOL_MINT, "SOL",
                    swap.from_amount, proceeds, tx, "swap_out", stats
                )

        # Acquire to_token
        if swap.to_mint and swap.to_amount and swap.to_amount > 0:
            # Cost basis is USD value of what was given up
            cost = swap.from_usd_value or swap.to_usd_value or Decimal("0")
            per_unit = cost / swap.to_amount if swap.to_amount > 0 else Decimal("0")

            acq_type = "swap_in"
            if swap.from_mint in STABLECOIN_MINTS or swap.from_mint == SOL_MINT:
                acq_type = "buy"

            self._create_lot(
                wallet, swap.to_mint, swap.to_symbol,
                swap.to_amount, per_unit, cost, tx, acq_type, stats
            )

    def _process_sol_transfer(
        self, wallet: Wallet, tx: Transaction, st: SolTransfer,
        tracked: set[str], tx_type: str, stats: dict
    ):
        """Process a SOL transfer for cost basis."""
        if not st.amount_sol or st.amount_sol <= 0:
            return

        # Check if dust
        usd = st.usd_value_at_time or Decimal("0")
        if 0 < float(usd) < DUST_THRESHOLD_USD:
            return

        if st.direction == "in":
            # Determine acquisition type
            is_own_transfer = st.counterparty_address in tracked
            if is_own_transfer:
                acq_type = "transfer_in"
            elif tx_type == "STAKING_REWARD":
                acq_type = "staking_reward"
            elif tx_type == "AIRDROP":
                acq_type = "airdrop"
            else:
                acq_type = "transfer_in"

            per_unit = usd / st.amount_sol if st.amount_sol > 0 else Decimal("0")
            self._create_lot(
                wallet, SOL_MINT, "SOL", st.amount_sol, per_unit, usd, tx, acq_type, stats
            )

        elif st.direction == "out":
            is_own_transfer = st.counterparty_address in tracked
            if is_own_transfer:
                return  # Don't treat transfers between own wallets as disposals

            disposal_type = "transfer_out"
            proceeds = usd
            self._create_disposal(
                wallet, SOL_MINT, "SOL", st.amount_sol, proceeds, tx, disposal_type, stats
            )

    def _process_token_transfer(
        self, wallet: Wallet, tx: Transaction, tt: TokenTransfer,
        tracked: set[str], tx_type: str, stats: dict
    ):
        """Process a token transfer for cost basis."""
        if not tt.amount or tt.amount <= 0:
            return
        if tt.mint_address in STABLECOIN_MINTS:
            return  # Don't track stablecoin cost basis

        usd = tt.usd_value_at_time or Decimal("0")
        if 0 < float(usd) < DUST_THRESHOLD_USD:
            return

        if tt.direction == "in":
            is_own_transfer = tt.counterparty_address in tracked
            if is_own_transfer:
                acq_type = "transfer_in"
            elif tx_type == "STAKING_REWARD":
                acq_type = "staking_reward"
            elif tx_type == "AIRDROP":
                acq_type = "airdrop"
            else:
                acq_type = "transfer_in"

            per_unit = usd / tt.amount if tt.amount > 0 else Decimal("0")
            self._create_lot(
                wallet, tt.mint_address, tt.token_symbol,
                tt.amount, per_unit, usd, tx, acq_type, stats
            )

        elif tt.direction == "out":
            is_own_transfer = tt.counterparty_address in tracked
            if is_own_transfer:
                return

            proceeds = usd
            self._create_disposal(
                wallet, tt.mint_address, tt.token_symbol,
                tt.amount, proceeds, tx, "transfer_out", stats
            )

    def _create_lot(
        self, wallet: Wallet, mint: str, symbol: str | None,
        amount: Decimal, per_unit_usd: Decimal, total_usd: Decimal,
        tx: Transaction, acq_type: str, stats: dict
    ):
        """Create a new cost basis lot."""
        lot = CostBasisLot(
            wallet_id=wallet.id,
            mint_address=mint,
            token_symbol=symbol,
            acquisition_date=tx.block_time or datetime.utcnow(),
            acquisition_amount=amount,
            remaining_amount=amount,
            cost_basis_per_unit_usd=per_unit_usd,
            total_cost_basis_usd=total_usd,
            acquisition_tx_signature=tx.signature,
            acquisition_type=acq_type,
            disposed=False,
        )
        self.session.add(lot)
        self.session.flush()
        stats["lots_created"] += 1

    def _create_disposal(
        self, wallet: Wallet, mint: str, symbol: str | None,
        amount: Decimal, proceeds: Decimal, tx: Transaction,
        disposal_type: str, stats: dict
    ):
        """Create disposals by matching against cost basis lots (FIFO/LIFO)."""
        remaining = amount

        # Get available lots
        query = (
            self.session.query(CostBasisLot)
            .filter(
                CostBasisLot.wallet_id == wallet.id,
                CostBasisLot.mint_address == mint,
                CostBasisLot.remaining_amount > 0,
                CostBasisLot.disposed == False,
            )
        )

        if self.method == "fifo":
            query = query.order_by(CostBasisLot.acquisition_date.asc())
        else:  # lifo
            query = query.order_by(CostBasisLot.acquisition_date.desc())

        lots = query.all()

        if not lots:
            # No lots available — create a zero-cost-basis disposal
            disposal_date = tx.block_time or datetime.utcnow()
            # Create a synthetic lot with zero cost basis
            zero_lot = CostBasisLot(
                wallet_id=wallet.id,
                mint_address=mint,
                token_symbol=symbol,
                acquisition_date=disposal_date,
                acquisition_amount=amount,
                remaining_amount=Decimal("0"),
                cost_basis_per_unit_usd=Decimal("0"),
                total_cost_basis_usd=Decimal("0"),
                acquisition_tx_signature=tx.signature,
                acquisition_type="unknown",
                disposed=True,
            )
            self.session.add(zero_lot)
            self.session.flush()
            stats["lots_created"] += 1

            holding_period = "short"
            disposal = Disposal(
                wallet_id=wallet.id,
                cost_basis_lot_id=zero_lot.id,
                mint_address=mint,
                token_symbol=symbol,
                disposal_date=disposal_date,
                disposal_amount=amount,
                proceeds_usd=proceeds,
                cost_basis_usd=Decimal("0"),
                gain_loss_usd=proceeds,
                holding_period=holding_period,
                disposal_type=disposal_type,
                disposal_tx_signature=tx.signature,
            )
            self.session.add(disposal)
            stats["disposals_created"] += 1
            return

        # Proportionally allocate proceeds across consumed lots
        total_disposed = Decimal("0")

        for lot in lots:
            if remaining <= 0:
                break

            consume = min(remaining, lot.remaining_amount)
            proportion = consume / amount if amount > 0 else Decimal("0")
            lot_proceeds = proceeds * proportion
            lot_cost = lot.cost_basis_per_unit_usd * consume

            disposal_date = tx.block_time or datetime.utcnow()
            acq_date = lot.acquisition_date

            days_held = (disposal_date - acq_date).days if acq_date else 0
            holding_period = "long" if days_held > LONG_TERM_HOLDING_DAYS else "short"

            disposal = Disposal(
                wallet_id=wallet.id,
                cost_basis_lot_id=lot.id,
                mint_address=mint,
                token_symbol=symbol,
                disposal_date=disposal_date,
                disposal_amount=consume,
                proceeds_usd=lot_proceeds,
                cost_basis_usd=lot_cost,
                gain_loss_usd=lot_proceeds - lot_cost,
                holding_period=holding_period,
                disposal_type=disposal_type,
                disposal_tx_signature=tx.signature,
            )
            self.session.add(disposal)
            stats["disposals_created"] += 1

            lot.remaining_amount -= consume
            if lot.remaining_amount <= 0:
                lot.disposed = True

            remaining -= consume
            total_disposed += consume
