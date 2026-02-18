"""FIFO/LIFO cost basis engine for tracking token acquisitions and disposals."""
import logging
from collections import defaultdict
from datetime import datetime
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
        """
        Process a single transaction for cost basis using net token flows.

        Instead of processing swaps and transfers separately (which breaks on
        DEX routing artifacts where tokens pass through the wallet), compute
        the NET flow per mint and create lots/disposals from those.
        Swap records are used only for determining acquisition types and cost basis.
        """
        tx_type = tx.tx_type

        # Gather all movements
        token_transfers = self.session.query(TokenTransfer).filter(TokenTransfer.transaction_id == tx.id).all()
        sol_transfers = self.session.query(SolTransfer).filter(SolTransfer.transaction_id == tx.id).all()
        swaps = self.session.query(Swap).filter(Swap.transaction_id == tx.id).all()

        # Compute net flows per mint
        flows = defaultdict(lambda: {
            "net": Decimal("0"), "usd_in": Decimal("0"), "usd_out": Decimal("0"),
            "symbol": None, "counterparties_in": set(), "counterparties_out": set(),
        })

        for tt in token_transfers:
            # Skip SOL token transfers — SOL is already tracked via SolTransfers
            # (wSOL wrapping/unwrapping creates both native and token transfers)
            if tt.mint_address == SOL_MINT:
                continue
            f = flows[tt.mint_address]
            f["symbol"] = f["symbol"] or tt.token_symbol
            usd = tt.usd_value_at_time or Decimal("0")
            if tt.direction == "in":
                f["net"] += tt.amount
                f["usd_in"] += usd
                if tt.counterparty_address:
                    f["counterparties_in"].add(tt.counterparty_address)
            else:
                f["net"] -= tt.amount
                f["usd_out"] += usd
                if tt.counterparty_address:
                    f["counterparties_out"].add(tt.counterparty_address)

        for st in sol_transfers:
            f = flows[SOL_MINT]
            f["symbol"] = "SOL"
            usd = st.usd_value_at_time or Decimal("0")
            if st.direction == "in":
                f["net"] += st.amount_sol
                f["usd_in"] += usd
                if st.counterparty_address:
                    f["counterparties_in"].add(st.counterparty_address)
            else:
                f["net"] -= st.amount_sol
                f["usd_out"] += usd
                if st.counterparty_address:
                    f["counterparties_out"].add(st.counterparty_address)

        # Deduct tx fee from SOL (fees reduce balance but aren't in SolTransfer records)
        if tx.fee_sol and tx.fee_sol > 0:
            flows[SOL_MINT]["net"] -= tx.fee_sol
            flows[SOL_MINT]["usd_out"] += tx.fee_usd or Decimal("0")
            flows[SOL_MINT]["symbol"] = "SOL"

        # Build swap context for determining types and cost basis
        swap_ctx = {}
        for swap in swaps:
            if swap.to_mint and swap.to_amount:
                acq_type = "swap_in"
                if swap.from_mint in STABLECOIN_MINTS or swap.from_mint == SOL_MINT:
                    acq_type = "buy"
                swap_ctx.setdefault(swap.to_mint, {}).update({
                    "acq_type": acq_type,
                    "cost_usd": swap.from_usd_value or swap.to_usd_value or Decimal("0"),
                    "swap_to_amount": swap.to_amount,
                })
            if swap.from_mint and swap.from_amount:
                ctx = swap_ctx.setdefault(swap.from_mint, {})
                ctx["disposal_type"] = "swap_out"
                ctx["proceeds_usd"] = swap.from_usd_value or Decimal("0")
                ctx["swap_from_amount"] = swap.from_amount

        # Process net flows
        for mint, f in flows.items():
            net = f["net"]
            symbol = f["symbol"]

            if net == 0 or mint in STABLECOIN_MINTS:
                continue

            net_usd = f["usd_in"] - f["usd_out"]

            if net > 0:
                # --- Acquisition ---
                # Determine type
                all_own = f["counterparties_in"] and f["counterparties_in"].issubset(tracked)
                ctx = swap_ctx.get(mint, {})

                if all_own:
                    acq_type = "transfer_in"
                elif "acq_type" in ctx:
                    acq_type = ctx["acq_type"]
                elif tx_type == "STAKING_REWARD":
                    acq_type = "staking_reward"
                elif tx_type == "AIRDROP":
                    acq_type = "airdrop"
                else:
                    acq_type = "transfer_in"

                # Determine cost basis
                if "cost_usd" in ctx:
                    cost = ctx["cost_usd"]
                    # Scale if net differs from swap amount (partial routing)
                    swap_amt = ctx.get("swap_to_amount", net)
                    if swap_amt and swap_amt > 0 and net != swap_amt:
                        cost = cost * net / swap_amt
                else:
                    cost = abs(net_usd)

                if 0 < float(abs(net_usd)) < DUST_THRESHOLD_USD:
                    continue

                per_unit = cost / net if net > 0 else Decimal("0")
                self._create_lot(wallet, mint, symbol, net, per_unit, cost, tx, acq_type, stats)

            else:
                # --- Disposal ---
                amount = abs(net)

                # Skip own-wallet transfers
                all_own = f["counterparties_out"] and f["counterparties_out"].issubset(tracked)
                if all_own:
                    continue

                ctx = swap_ctx.get(mint, {})
                if "disposal_type" in ctx:
                    disposal_type = ctx["disposal_type"]
                    proceeds = ctx.get("proceeds_usd", abs(net_usd))
                    # Scale if net differs from swap amount
                    swap_amt = ctx.get("swap_from_amount", amount)
                    if swap_amt and swap_amt > 0 and amount != swap_amt:
                        proceeds = proceeds * amount / swap_amt
                else:
                    disposal_type = "transfer_out"
                    proceeds = abs(net_usd)

                if 0 < float(abs(net_usd)) < DUST_THRESHOLD_USD:
                    continue

                self._create_disposal(wallet, mint, symbol, amount, proceeds, tx, disposal_type, stats)

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
