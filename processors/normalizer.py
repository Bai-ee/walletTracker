"""Convert raw Helius parsed transaction data into structured database records."""
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from database.models import (
    Wallet, Transaction, TokenTransfer, SolTransfer, Swap
)
from fetchers.token_registry import get_token_info, get_symbol
from fetchers.price_fetcher import PriceFetcher
from config import KNOWN_DEX_PROGRAMS

logger = logging.getLogger(__name__)

SOL_MINT = "So11111111111111111111111111111111111111112"


class TransactionNormalizer:
    def __init__(self, session: Session, price_fetcher: Optional[PriceFetcher] = None):
        self.session = session
        self.price_fetcher = price_fetcher

    def normalize_wallet_transactions(self, wallet: Wallet, fetch_prices: bool = True) -> int:
        """
        Process all un-normalized transactions for a wallet.
        Returns count of newly processed transactions.
        """
        # Get transactions that haven't been normalized yet (no child records)
        transactions = (
            self.session.query(Transaction)
            .filter(
                Transaction.wallet_id == wallet.id,
                Transaction.raw_data.isnot(None),
            )
            .order_by(Transaction.block_time.asc())
            .all()
        )

        processed = 0
        for tx in transactions:
            # Skip if already has child records
            if tx.sol_transfers or tx.token_transfers:
                continue

            try:
                self._normalize_transaction(wallet, tx, fetch_prices)
                processed += 1
                if processed % 100 == 0:
                    self.session.commit()
                    logger.info(f"Normalized {processed} transactions for {wallet.address[:8]}...")
            except Exception as e:
                logger.error(f"Error normalizing tx {tx.signature}: {e}")
                continue

        self.session.commit()
        logger.info(f"Normalized {processed} transactions for {wallet.address[:8]}...")
        return processed

    def _normalize_transaction(self, wallet: Wallet, tx: Transaction, fetch_prices: bool):
        """Normalize a single transaction into structured records."""
        raw = tx.raw_data
        if not raw:
            return

        wallet_addr = wallet.address

        # Process native SOL transfers
        native_transfers = raw.get("nativeTransfers", [])
        for nt in native_transfers:
            from_addr = nt.get("fromUserAccount", "")
            to_addr = nt.get("toUserAccount", "")
            amount_lamports = nt.get("amount", 0)

            if from_addr != wallet_addr and to_addr != wallet_addr:
                continue

            amount_sol = Decimal(str(amount_lamports)) / Decimal("1000000000")
            if amount_sol == 0:
                continue

            direction = "out" if from_addr == wallet_addr else "in"
            counterparty = to_addr if direction == "out" else from_addr

            usd_value = None
            if fetch_prices and self.price_fetcher and tx.block_time:
                sol_price = self.price_fetcher.get_price_at_time(SOL_MINT, tx.block_time)
                if sol_price:
                    usd_value = amount_sol * sol_price

            sol_transfer = SolTransfer(
                transaction_id=tx.id,
                wallet_id=wallet.id,
                amount_sol=amount_sol,
                direction=direction,
                counterparty_address=counterparty,
                usd_value_at_time=usd_value,
            )
            self.session.add(sol_transfer)

        # Process token transfers
        token_transfers = raw.get("tokenTransfers", [])
        tokens_out = []
        tokens_in = []

        for tt in token_transfers:
            from_addr = tt.get("fromUserAccount", "")
            to_addr = tt.get("toUserAccount", "")
            mint = tt.get("mint", "")

            if from_addr != wallet_addr and to_addr != wallet_addr:
                continue

            raw_amount = tt.get("tokenAmount", 0)
            try:
                amount = Decimal(str(raw_amount))
            except (InvalidOperation, ValueError):
                amount = Decimal("0")

            if amount == 0:
                continue

            token_info = get_token_info(mint) if mint else None
            symbol = token_info["symbol"] if token_info else "???"
            name = token_info["name"] if token_info else "Unknown"
            decimals = token_info["decimals"] if token_info else 0

            direction = "out" if from_addr == wallet_addr else "in"
            counterparty = to_addr if direction == "out" else from_addr

            usd_value = None
            if fetch_prices and self.price_fetcher and tx.block_time:
                price = self.price_fetcher.get_price_at_time(mint, tx.block_time)
                if price:
                    usd_value = amount * price

            token_transfer = TokenTransfer(
                transaction_id=tx.id,
                wallet_id=wallet.id,
                mint_address=mint,
                token_symbol=symbol,
                token_name=name,
                decimals=decimals,
                amount=amount,
                direction=direction,
                counterparty_address=counterparty,
                usd_value_at_time=usd_value,
            )
            self.session.add(token_transfer)

            if direction == "out":
                tokens_out.append((mint, symbol, amount, usd_value))
            else:
                tokens_in.append((mint, symbol, amount, usd_value))

        # Detect swaps: wallet sends one token and receives another in the same tx
        if tokens_out and tokens_in:
            from_mint, from_symbol, from_amount, from_usd = tokens_out[0]
            to_mint, to_symbol, to_amount, to_usd = tokens_in[0]

            # Determine DEX program
            source = raw.get("source", "")
            dex = source if source else self._detect_dex(raw)

            swap = Swap(
                transaction_id=tx.id,
                wallet_id=wallet.id,
                from_mint=from_mint,
                from_symbol=from_symbol,
                from_amount=from_amount,
                from_usd_value=from_usd,
                to_mint=to_mint,
                to_symbol=to_symbol,
                to_amount=to_amount,
                to_usd_value=to_usd,
                dex_program=dex,
            )
            self.session.add(swap)

        # Also check for SOL-to-token or token-to-SOL swaps
        elif (tokens_out and not tokens_in) or (tokens_in and not tokens_out):
            sol_in = [st for st in (tx.sol_transfers or []) if st.direction == "in"]
            sol_out = [st for st in (tx.sol_transfers or []) if st.direction == "out"]

            # Get freshly added sol_transfers from session
            self.session.flush()
            sol_transfers_new = (
                self.session.query(SolTransfer)
                .filter(SolTransfer.transaction_id == tx.id)
                .all()
            )
            sol_in_new = [s for s in sol_transfers_new if s.direction == "in"]
            sol_out_new = [s for s in sol_transfers_new if s.direction == "out"]

            source = raw.get("source", "")
            helius_type = raw.get("type", "")

            if helius_type == "SWAP" or source in ("JUPITER", "RAYDIUM", "ORCA"):
                if tokens_out and sol_in_new:
                    # Token out, SOL in = sell
                    from_mint, from_symbol, from_amount, from_usd = tokens_out[0]
                    sol_total = sum(s.amount_sol for s in sol_in_new)
                    sol_usd = sum(s.usd_value_at_time or 0 for s in sol_in_new)

                    swap = Swap(
                        transaction_id=tx.id,
                        wallet_id=wallet.id,
                        from_mint=from_mint,
                        from_symbol=from_symbol,
                        from_amount=from_amount,
                        from_usd_value=from_usd,
                        to_mint=SOL_MINT,
                        to_symbol="SOL",
                        to_amount=sol_total,
                        to_usd_value=sol_usd,
                        dex_program=source,
                    )
                    self.session.add(swap)

                elif tokens_in and sol_out_new:
                    # SOL out, token in = buy
                    to_mint, to_symbol, to_amount, to_usd = tokens_in[0]
                    sol_total = sum(s.amount_sol for s in sol_out_new)
                    sol_usd = sum(s.usd_value_at_time or 0 for s in sol_out_new)

                    swap = Swap(
                        transaction_id=tx.id,
                        wallet_id=wallet.id,
                        from_mint=SOL_MINT,
                        from_symbol="SOL",
                        from_amount=sol_total,
                        from_usd_value=sol_usd,
                        to_mint=to_mint,
                        to_symbol=to_symbol,
                        to_amount=to_amount,
                        to_usd_value=to_usd,
                        dex_program=source,
                    )
                    self.session.add(swap)

        # Update fee USD value
        if fetch_prices and self.price_fetcher and tx.block_time and tx.fee_sol:
            sol_price = self.price_fetcher.get_price_at_time(SOL_MINT, tx.block_time)
            if sol_price:
                tx.fee_usd = tx.fee_sol * sol_price

    def _detect_dex(self, raw_data: dict) -> str:
        """Try to detect the DEX from account keys in the transaction."""
        account_data = raw_data.get("accountData", [])
        for ad in account_data:
            account = ad.get("account", "")
            if account in KNOWN_DEX_PROGRAMS:
                return KNOWN_DEX_PROGRAMS[account]
        return ""
