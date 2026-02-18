"""Transaction type classification engine."""
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from database.models import Wallet, Transaction, Swap, TokenTransfer, SolTransfer
from config import (
    STABLECOIN_MINTS, KNOWN_STAKING_PROGRAMS, KNOWN_NFT_MARKETPLACES,
    DUST_THRESHOLD_USD
)

logger = logging.getLogger(__name__)

SOL_MINT = "So11111111111111111111111111111111111111112"
STABLECOIN_OR_SOL = STABLECOIN_MINTS | {SOL_MINT}


class TransactionClassifier:
    def __init__(self, session: Session, tracked_wallets: set[str] | None = None):
        self.session = session
        self.tracked_wallets = tracked_wallets or set()

    def classify_all(self, wallet: Wallet) -> int:
        """Classify all transactions for a wallet. Returns count of classified transactions."""
        transactions = (
            self.session.query(Transaction)
            .filter(Transaction.wallet_id == wallet.id)
            .order_by(Transaction.block_time.asc())
            .all()
        )

        classified = 0
        for tx in transactions:
            old_type = tx.tx_type
            new_type = self._classify(wallet, tx)
            if new_type != old_type:
                tx.tx_type = new_type
                classified += 1

        self.session.commit()
        logger.info(f"Classified {classified} transactions for {wallet.address[:8]}...")
        return classified

    def _classify(self, wallet: Wallet, tx: Transaction) -> str:
        """Classify a single transaction."""
        raw = tx.raw_data or {}
        helius_type = raw.get("type", "")
        source = raw.get("source", "")

        # Check for swap first (token A out + token B in)
        swaps = (
            self.session.query(Swap)
            .filter(Swap.transaction_id == tx.id)
            .all()
        )
        if swaps:
            swap = swaps[0]
            return self._classify_swap(swap)

        # Get transfers
        sol_transfers = (
            self.session.query(SolTransfer)
            .filter(SolTransfer.transaction_id == tx.id)
            .all()
        )
        token_transfers = (
            self.session.query(TokenTransfer)
            .filter(TokenTransfer.transaction_id == tx.id)
            .all()
        )

        # Check Helius type hints
        if helius_type in ("STAKE", "UNSTAKE"):
            return "STAKING_REWARD" if helius_type == "UNSTAKE" else helius_type

        if helius_type == "NFT_SALE":
            has_out = any(t.direction == "out" for t in token_transfers)
            return "NFT_SALE" if has_out else "NFT_PURCHASE"

        if helius_type in ("NFT_MINT", "COMPRESSED_NFT_MINT"):
            return "NFT_PURCHASE"

        if helius_type == "NFT_BID":
            return "NFT_PURCHASE"

        # Check for staking program interactions
        if source in KNOWN_STAKING_PROGRAMS or any(
            ad.get("account", "") in KNOWN_STAKING_PROGRAMS
            for ad in raw.get("accountData", [])
        ):
            # If we received tokens, it's a staking reward
            if any(t.direction == "in" for t in token_transfers):
                return "STAKING_REWARD"

        # Token transfers
        if token_transfers and not sol_transfers:
            return self._classify_token_transfer(wallet, token_transfers)

        # SOL-only transfers
        if sol_transfers and not token_transfers:
            return self._classify_sol_transfer(wallet, sol_transfers)

        # Mixed but not swap (already handled above)
        if sol_transfers and token_transfers:
            return self._classify_mixed(wallet, sol_transfers, token_transfers, helius_type)

        # Check for airdrop hints
        if helius_type == "TRANSFER" and not sol_transfers and not token_transfers:
            return "FEE"  # Just a fee transaction

        # Fee-only or empty transactions
        if not sol_transfers and not token_transfers:
            return "FEE"

        return "UNKNOWN"

    def _classify_swap(self, swap: Swap) -> str:
        """Classify a swap transaction."""
        from_is_base = swap.from_mint in STABLECOIN_OR_SOL
        to_is_base = swap.to_mint in STABLECOIN_OR_SOL

        if from_is_base and not to_is_base:
            return "BUY"
        elif not from_is_base and to_is_base:
            return "SELL"
        else:
            return "SWAP"

    def _classify_token_transfer(self, wallet: Wallet, transfers: list[TokenTransfer]) -> str:
        """Classify a token-only transfer."""
        directions = set(t.direction for t in transfers)

        if directions == {"in"}:
            # Received tokens - check if from tracked wallet (transfer) or external (income/airdrop)
            counterparties = set(t.counterparty_address for t in transfers)
            if counterparties & self.tracked_wallets:
                return "TOKEN_TRANSFER_IN"

            # Check if it's dust
            total_usd = sum(t.usd_value_at_time or 0 for t in transfers)
            if 0 < total_usd < DUST_THRESHOLD_USD:
                return "DUST"

            # Check for NFT (decimals=0, amount=1)
            if any(t.decimals == 0 and t.amount == 1 for t in transfers):
                return "AIRDROP"  # NFT airdrop

            return "TOKEN_TRANSFER_IN"

        elif directions == {"out"}:
            counterparties = set(t.counterparty_address for t in transfers)
            if counterparties & self.tracked_wallets:
                return "TOKEN_TRANSFER_OUT"
            return "TOKEN_TRANSFER_OUT"

        return "UNKNOWN"

    def _classify_sol_transfer(self, wallet: Wallet, transfers: list[SolTransfer]) -> str:
        """Classify a SOL-only transfer."""
        directions = set(t.direction for t in transfers)
        total_amount = sum(t.amount_sol for t in transfers)

        if directions == {"in"}:
            counterparties = set(t.counterparty_address for t in transfers)
            if counterparties & self.tracked_wallets:
                return "SOL_TRANSFER_IN"

            total_usd = sum(t.usd_value_at_time or 0 for t in transfers)
            if 0 < total_usd < DUST_THRESHOLD_USD:
                return "DUST"

            return "SOL_TRANSFER_IN"

        elif directions == {"out"}:
            counterparties = set(t.counterparty_address for t in transfers)
            if counterparties & self.tracked_wallets:
                return "SOL_TRANSFER_OUT"
            return "SOL_TRANSFER_OUT"

        # Both in and out — net transfer
        return "SOL_TRANSFER_OUT" if total_amount > 0 else "SOL_TRANSFER_IN"

    def _classify_mixed(
        self,
        wallet: Wallet,
        sol_transfers: list[SolTransfer],
        token_transfers: list[TokenTransfer],
        helius_type: str,
    ) -> str:
        """Classify transaction with both SOL and token movements."""
        token_dirs = set(t.direction for t in token_transfers)
        sol_dirs = set(t.direction for t in sol_transfers)

        # NFT marketplace interactions
        if helius_type in ("NFT_SALE", "NFT_LISTING", "NFT_BID"):
            return helius_type

        # Token in + SOL out could be a buy
        if "in" in token_dirs and "out" in sol_dirs:
            return "BUY"

        # Token out + SOL in could be a sell
        if "out" in token_dirs and "in" in sol_dirs:
            return "SELL"

        return "UNKNOWN"
