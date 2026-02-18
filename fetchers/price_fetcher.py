"""Historical price lookups with aggressive caching."""
import time
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from config import (
    COINGECKO_BASE_URL, COINGECKO_API_KEY, COINGECKO_RATE_LIMIT_DELAY,
    JUPITER_PRICE_URL, STABLECOIN_MINTS, MINT_TO_COINGECKO
)
from database.models import PriceCache

logger = logging.getLogger(__name__)


class PriceFetcher:
    def __init__(self, session: Session):
        self.session = session
        self._last_cg_request = 0.0
        # In-memory cache to avoid repeated DB lookups
        self._mem_cache: dict[tuple[str, date], Optional[Decimal]] = {}

    def get_price(self, mint_address: str, target_date: date) -> Optional[Decimal]:
        """
        Get USD price for a token on a specific date.
        Returns Decimal price or None if unavailable.
        """
        # Stablecoins are always $1
        if mint_address in STABLECOIN_MINTS:
            return Decimal("1.00")

        cache_key = (mint_address, target_date)
        if cache_key in self._mem_cache:
            return self._mem_cache[cache_key]

        # Check DB cache
        cached = (
            self.session.query(PriceCache)
            .filter(PriceCache.mint_address == mint_address, PriceCache.date == target_date)
            .first()
        )
        if cached:
            price = Decimal(str(cached.price_usd)) if cached.price_usd is not None else None
            self._mem_cache[cache_key] = price
            return price

        # Fetch from CoinGecko
        price = self._fetch_coingecko(mint_address, target_date)

        # Fallback to Jupiter (current price only, useful for recent dates)
        if price is None and (date.today() - target_date).days <= 1:
            price = self._fetch_jupiter(mint_address)

        # Cache the result (even None to avoid re-fetching)
        self._cache_price(mint_address, target_date, price, "coingecko" if price else "none")
        self._mem_cache[cache_key] = price
        return price

    def get_price_at_time(self, mint_address: str, dt: datetime) -> Optional[Decimal]:
        """Get price for a token at a specific datetime (uses date granularity)."""
        return self.get_price(mint_address, dt.date())

    def _coingecko_rate_limit(self):
        elapsed = time.time() - self._last_cg_request
        if elapsed < COINGECKO_RATE_LIMIT_DELAY:
            time.sleep(COINGECKO_RATE_LIMIT_DELAY - elapsed)
        self._last_cg_request = time.time()

    def _fetch_coingecko(self, mint_address: str, target_date: date) -> Optional[Decimal]:
        """Fetch historical price from CoinGecko."""
        coingecko_id = MINT_TO_COINGECKO.get(mint_address)
        if not coingecko_id:
            return None

        date_str = target_date.strftime("%d-%m-%Y")
        url = f"{COINGECKO_BASE_URL}/coins/{coingecko_id}/history"
        params = {"date": date_str, "localization": "false"}
        if COINGECKO_API_KEY:
            params["x_cg_demo_api_key"] = COINGECKO_API_KEY

        self._coingecko_rate_limit()
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            market_data = data.get("market_data", {})
            current_price = market_data.get("current_price", {})
            usd_price = current_price.get("usd")
            if usd_price is not None:
                return Decimal(str(usd_price))
        except Exception as e:
            logger.debug(f"CoinGecko price fetch failed for {mint_address} on {target_date}: {e}")

        return None

    def _fetch_jupiter(self, mint_address: str) -> Optional[Decimal]:
        """Fetch current price from Jupiter Price API."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(JUPITER_PRICE_URL, params={"ids": mint_address})
                resp.raise_for_status()
                data = resp.json()

            price_data = data.get("data", {}).get(mint_address)
            if price_data and price_data.get("price"):
                return Decimal(str(price_data["price"]))
        except Exception as e:
            logger.debug(f"Jupiter price fetch failed for {mint_address}: {e}")

        return None

    def _cache_price(self, mint_address: str, target_date: date, price: Optional[Decimal], source: str):
        """Store price in database cache."""
        try:
            entry = PriceCache(
                mint_address=mint_address,
                date=target_date,
                price_usd=price,
                source=source,
            )
            self.session.merge(entry)
            self.session.commit()
        except Exception:
            self.session.rollback()
