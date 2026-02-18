"""Wallet interaction tracking and flow analysis."""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from database.models import (
    Wallet, Transaction, SolTransfer, TokenTransfer, WalletInteraction
)
from config import (
    KNOWN_DEX_PROGRAMS, KNOWN_CEX_ADDRESSES, KNOWN_NFT_MARKETPLACES,
    KNOWN_STAKING_PROGRAMS
)

logger = logging.getLogger(__name__)


def label_address(address: str) -> str | None:
    """Try to label a known address."""
    all_known = {}
    all_known.update(KNOWN_DEX_PROGRAMS)
    all_known.update(KNOWN_CEX_ADDRESSES)
    all_known.update(KNOWN_NFT_MARKETPLACES)
    all_known.update(KNOWN_STAKING_PROGRAMS)

    return all_known.get(address)


class WalletGraphBuilder:
    def __init__(self, session: Session):
        self.session = session

    def build_interactions(self, wallet: Wallet) -> int:
        """
        Build/rebuild the wallet interaction table for a given wallet.
        Returns number of unique counterparties found.
        """
        # Clear existing interactions for this wallet
        self.session.query(WalletInteraction).filter(
            WalletInteraction.wallet_id == wallet.id
        ).delete()
        self.session.flush()

        # Aggregate SOL transfers by counterparty
        counterparty_data: dict[str, dict] = {}

        sol_transfers = (
            self.session.query(SolTransfer)
            .filter(SolTransfer.wallet_id == wallet.id)
            .all()
        )

        for st in sol_transfers:
            cp = st.counterparty_address
            if not cp:
                continue
            if cp not in counterparty_data:
                counterparty_data[cp] = self._empty_interaction()

            data = counterparty_data[cp]
            if st.direction == "in":
                data["total_received_sol"] += st.amount_sol or 0
                data["total_received_usd"] += st.usd_value_at_time or 0
            else:
                data["total_sent_sol"] += st.amount_sol or 0
                data["total_sent_usd"] += st.usd_value_at_time or 0
            data["tx_count"] += 1

            tx = self.session.query(Transaction).get(st.transaction_id)
            if tx and tx.block_time:
                if data["first_interaction"] is None or tx.block_time < data["first_interaction"]:
                    data["first_interaction"] = tx.block_time
                if data["last_interaction"] is None or tx.block_time > data["last_interaction"]:
                    data["last_interaction"] = tx.block_time

        # Aggregate token transfers by counterparty
        token_transfers = (
            self.session.query(TokenTransfer)
            .filter(TokenTransfer.wallet_id == wallet.id)
            .all()
        )

        for tt in token_transfers:
            cp = tt.counterparty_address
            if not cp:
                continue
            if cp not in counterparty_data:
                counterparty_data[cp] = self._empty_interaction()

            data = counterparty_data[cp]
            if tt.direction == "in":
                data["token_transfers_in"] += 1
                data["total_received_usd"] += tt.usd_value_at_time or 0
            else:
                data["token_transfers_out"] += 1
                data["total_sent_usd"] += tt.usd_value_at_time or 0
            data["tx_count"] += 1

            tx = self.session.query(Transaction).get(tt.transaction_id)
            if tx and tx.block_time:
                if data["first_interaction"] is None or tx.block_time < data["first_interaction"]:
                    data["first_interaction"] = tx.block_time
                if data["last_interaction"] is None or tx.block_time > data["last_interaction"]:
                    data["last_interaction"] = tx.block_time

        # Save to DB
        for cp_address, data in counterparty_data.items():
            interaction = WalletInteraction(
                wallet_id=wallet.id,
                counterparty_address=cp_address,
                counterparty_label=label_address(cp_address),
                total_received_sol=data["total_received_sol"],
                total_sent_sol=data["total_sent_sol"],
                total_received_usd=data["total_received_usd"],
                total_sent_usd=data["total_sent_usd"],
                token_transfers_in=data["token_transfers_in"],
                token_transfers_out=data["token_transfers_out"],
                first_interaction=data["first_interaction"],
                last_interaction=data["last_interaction"],
                tx_count=data["tx_count"],
            )
            self.session.add(interaction)

        self.session.commit()
        logger.info(f"Built interaction graph for {wallet.address[:8]}...: {len(counterparty_data)} counterparties")
        return len(counterparty_data)

    def get_top_counterparties(self, wallet: Wallet, limit: int = 20) -> list[WalletInteraction]:
        """Get top counterparties sorted by total USD volume."""
        return (
            self.session.query(WalletInteraction)
            .filter(WalletInteraction.wallet_id == wallet.id)
            .order_by(
                (WalletInteraction.total_received_usd + WalletInteraction.total_sent_usd).desc()
            )
            .limit(limit)
            .all()
        )

    def get_graph_data(self, wallet: Wallet, limit: int = 50) -> dict:
        """Get graph data for visualization (vis.js format)."""
        interactions = self.get_top_counterparties(wallet, limit)

        nodes = [{"id": wallet.address, "label": wallet.label or wallet.address[:8] + "...", "group": "self"}]
        edges = []

        for interaction in interactions:
            cp = interaction.counterparty_address
            label = interaction.counterparty_label or cp[:8] + "..."

            group = "unknown"
            if interaction.counterparty_label:
                if any(x in interaction.counterparty_label.lower() for x in ("dex", "jupiter", "raydium", "orca")):
                    group = "dex"
                elif any(x in interaction.counterparty_label.lower() for x in ("coinbase", "binance", "kraken")):
                    group = "cex"
                elif any(x in interaction.counterparty_label.lower() for x in ("magic eden", "tensor", "hadeswap")):
                    group = "nft"
                elif any(x in interaction.counterparty_label.lower() for x in ("staking", "marinade", "jito")):
                    group = "staking"

            nodes.append({"id": cp, "label": label, "group": group})

            total_vol = float(interaction.total_received_usd + interaction.total_sent_usd)
            edges.append({
                "from": wallet.address,
                "to": cp,
                "value": total_vol,
                "title": f"${total_vol:,.2f} total volume, {interaction.tx_count} txs",
            })

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _empty_interaction() -> dict:
        return {
            "total_received_sol": Decimal("0"),
            "total_sent_sol": Decimal("0"),
            "total_received_usd": Decimal("0"),
            "total_sent_usd": Decimal("0"),
            "token_transfers_in": 0,
            "token_transfers_out": 0,
            "first_interaction": None,
            "last_interaction": None,
            "tx_count": 0,
        }
