"""Solscan API integration for supplemental data and token metadata."""
import logging
from typing import Optional

import httpx

from config import SOLSCAN_API_KEY

logger = logging.getLogger(__name__)

SOLSCAN_BASE = "https://pro-api.solscan.io/v2.0"


def get_token_meta(mint_address: str) -> Optional[dict]:
    """Fetch token metadata from Solscan."""
    if not SOLSCAN_API_KEY:
        return None

    try:
        headers = {"token": SOLSCAN_API_KEY}
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{SOLSCAN_BASE}/token/meta",
                params={"address": mint_address},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("success") and data.get("data"):
            meta = data["data"]
            return {
                "symbol": meta.get("symbol", "???"),
                "name": meta.get("name", "Unknown"),
                "decimals": meta.get("decimals", 0),
                "icon": meta.get("icon", ""),
            }
    except Exception as e:
        logger.debug(f"Solscan token meta fetch failed for {mint_address}: {e}")

    return None
