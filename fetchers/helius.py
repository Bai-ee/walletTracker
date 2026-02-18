"""Helius API integration for fetching parsed Solana transaction history."""
import time
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from config import (
    HELIUS_API_KEY, HELIUS_BASE_URL, HELIUS_PAGE_SIZE,
    HELIUS_MAX_RETRIES, HELIUS_RETRY_DELAYS, HELIUS_RATE_LIMIT
)
from database.models import Wallet, Transaction

logger = logging.getLogger(__name__)


class HeliusFetcher:
    def __init__(self, session: Session):
        self.session = session
        self.api_key = HELIUS_API_KEY
        self.base_url = HELIUS_BASE_URL
        self.page_size = HELIUS_PAGE_SIZE
        self.rate_limit_delay = 1.0 / HELIUS_RATE_LIMIT
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _fetch_page(self, address: str, before: Optional[str] = None) -> list[dict]:
        """Fetch a single page of transactions from Helius with retry logic."""
        url = f"{self.base_url}/addresses/{address}/transactions"
        params = {"api-key": self.api_key, "limit": self.page_size}
        if before:
            params["before"] = before

        for attempt in range(HELIUS_MAX_RETRIES):
            self._rate_limit()
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                delay = HELIUS_RETRY_DELAYS[attempt] if attempt < len(HELIUS_RETRY_DELAYS) else 4
                logger.warning(
                    f"Helius API error (attempt {attempt + 1}/{HELIUS_MAX_RETRIES}): {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)

        logger.error(f"Failed to fetch transactions for {address} after {HELIUS_MAX_RETRIES} attempts")
        return []

    def fetch_all_transactions(
        self,
        wallet: Wallet,
        resume_from: Optional[str] = None,
        progress_callback=None
    ) -> int:
        """
        Fetch ALL transactions for a wallet, paginating through the entire history.
        Returns the number of new transactions fetched.
        """
        address = wallet.address
        before = resume_from
        total_fetched = 0
        new_count = 0

        # Find existing signatures to avoid duplicates
        existing_sigs = set(
            row[0] for row in
            self.session.query(Transaction.signature)
            .filter(Transaction.wallet_id == wallet.id)
            .all()
        )

        logger.info(f"Starting transaction fetch for {address[:8]}... ({len(existing_sigs)} existing)")

        while True:
            page = self._fetch_page(address, before=before)
            if not page:
                break

            batch = []
            for tx_data in page:
                sig = tx_data.get("signature")
                if not sig or sig in existing_sigs:
                    continue

                existing_sigs.add(sig)
                timestamp = tx_data.get("timestamp")
                block_time = datetime.utcfromtimestamp(timestamp) if timestamp else None
                fee_lamports = tx_data.get("fee", 0)
                fee_sol = Decimal(str(fee_lamports)) / Decimal("1000000000")

                tx = Transaction(
                    signature=sig,
                    wallet_id=wallet.id,
                    block_time=block_time,
                    slot=tx_data.get("slot"),
                    fee_sol=fee_sol,
                    tx_type=tx_data.get("type", "UNKNOWN"),
                    success=tx_data.get("transactionError") is None,
                    raw_data=tx_data,
                )
                batch.append(tx)

            if batch:
                self.session.bulk_save_objects(batch)
                self.session.commit()
                new_count += len(batch)

            total_fetched += len(page)

            if progress_callback:
                progress_callback(total_fetched, new_count)

            logger.info(f"Fetched {total_fetched} transactions for {address[:8]}... ({new_count} new)")

            # Use the last signature as the pagination cursor
            last_sig = page[-1].get("signature")
            if not last_sig or len(page) < self.page_size:
                break
            before = last_sig

        # Update wallet scan metadata
        wallet.last_scanned = datetime.utcnow()
        if not wallet.first_seen and new_count > 0:
            earliest = (
                self.session.query(Transaction.block_time)
                .filter(Transaction.wallet_id == wallet.id, Transaction.block_time.isnot(None))
                .order_by(Transaction.block_time.asc())
                .first()
            )
            if earliest:
                wallet.first_seen = earliest[0]
        self.session.commit()

        logger.info(f"Completed fetch for {address[:8]}...: {new_count} new transactions ({total_fetched} total scanned)")
        return new_count

    def get_last_signature(self, wallet: Wallet) -> Optional[str]:
        """Get the most recent transaction signature for resume capability."""
        result = (
            self.session.query(Transaction.signature)
            .filter(Transaction.wallet_id == wallet.id)
            .order_by(Transaction.block_time.desc())
            .first()
        )
        return result[0] if result else None
