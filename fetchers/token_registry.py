"""Token mint address → metadata mapping with local caching."""
import logging
from typing import Optional

import httpx

from config import HELIUS_API_KEY, HELIUS_RPC_URL

logger = logging.getLogger(__name__)

# Pre-populated known tokens
KNOWN_TOKENS: dict[str, dict] = {
    "So11111111111111111111111111111111111111112": {
        "symbol": "SOL",
        "name": "Wrapped SOL",
        "decimals": 9,
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": 6,
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "symbol": "USDT",
        "name": "Tether USD",
        "decimals": 6,
    },
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {
        "symbol": "mSOL",
        "name": "Marinade staked SOL",
        "decimals": 9,
    },
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {
        "symbol": "stSOL",
        "name": "Lido Staked SOL",
        "decimals": 9,
    },
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {
        "symbol": "JitoSOL",
        "name": "Jito Staked SOL",
        "decimals": 9,
    },
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {
        "symbol": "BONK",
        "name": "Bonk",
        "decimals": 5,
    },
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": {
        "symbol": "JUP",
        "name": "Jupiter",
        "decimals": 6,
    },
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": {
        "symbol": "WETH",
        "name": "Wrapped Ether (Wormhole)",
        "decimals": 8,
    },
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof": {
        "symbol": "RNDR",
        "name": "Render Token",
        "decimals": 8,
    },
}

# In-memory cache for tokens discovered at runtime
_token_cache: dict[str, dict] = dict(KNOWN_TOKENS)


def get_token_info(mint_address: str) -> Optional[dict]:
    """
    Get token metadata for a mint address.
    Returns dict with 'symbol', 'name', 'decimals' or None.
    """
    if mint_address in _token_cache:
        return _token_cache[mint_address]

    # Try Helius getAsset RPC
    info = _fetch_from_helius(mint_address)
    if info:
        _token_cache[mint_address] = info
        return info

    # Fallback: return basic info with unknown symbol
    fallback = {
        "symbol": mint_address[:6] + "...",
        "name": "Unknown Token",
        "decimals": 0,
    }
    _token_cache[mint_address] = fallback
    return fallback


def _fetch_from_helius(mint_address: str) -> Optional[dict]:
    """Fetch token metadata from Helius DAS API."""
    if not HELIUS_API_KEY:
        return None

    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "token-registry",
            "method": "getAsset",
            "params": {"id": mint_address},
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(HELIUS_RPC_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("result", {})
        content = result.get("content", {})
        metadata = content.get("metadata", {})
        token_info = result.get("token_info", {})

        symbol = metadata.get("symbol") or token_info.get("symbol", "???")
        name = metadata.get("name") or "Unknown Token"
        decimals = token_info.get("decimals", 0)

        return {"symbol": symbol, "name": name, "decimals": decimals}
    except Exception as e:
        logger.debug(f"Failed to fetch token info for {mint_address}: {e}")
        return None


def get_decimals(mint_address: str) -> int:
    """Get decimal places for a token. Defaults to 0 if unknown."""
    info = get_token_info(mint_address)
    return info["decimals"] if info else 0


def get_symbol(mint_address: str) -> str:
    """Get symbol for a token."""
    info = get_token_info(mint_address)
    return info["symbol"] if info else "???"


def register_token(mint_address: str, symbol: str, name: str, decimals: int):
    """Manually register a token in the cache."""
    _token_cache[mint_address] = {
        "symbol": symbol,
        "name": name,
        "decimals": decimals,
    }
