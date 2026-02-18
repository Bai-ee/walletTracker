import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
SOLSCAN_API_KEY = os.getenv("SOLSCAN_API_KEY", "")

# Helius
HELIUS_BASE_URL = "https://api-mainnet.helius-rpc.com/v0"
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_RATE_LIMIT = 10  # requests per second
HELIUS_PAGE_SIZE = 100  # max transactions per request
HELIUS_MAX_RETRIES = 3
HELIUS_RETRY_DELAYS = [1, 2, 4]  # seconds

# CoinGecko
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_RATE_LIMIT_DELAY = 2.5  # seconds between requests (free tier)

# Jupiter Price API
JUPITER_PRICE_URL = "https://price.jup.ag/v6/price"

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///solana_scanner.db")

# Known stablecoins (hardcode $1.00)
STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
}

# Known token mint -> CoinGecko ID mapping
MINT_TO_COINGECKO = {
    "So11111111111111111111111111111111111111112": "solana",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "usd-coin",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "tether",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "msol",
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "lido-staked-sol",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jito-staked-sol",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "bonk",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "jupiter-exchange-solana",
}

# Known DEX program addresses
KNOWN_DEX_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca Token Swap v2",
    "SSwpkEEcbUqx4vtoEByFjSkhKdCT862DNVb52nZg1UZ": "Saber Stable Swap",
}

# Known CEX deposit addresses (partial list)
KNOWN_CEX_ADDRESSES = {
    # These are commonly known hot wallets; add more as needed
}

# Known NFT marketplace programs
KNOWN_NFT_MARKETPLACES = {
    "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K": "Magic Eden v2",
    "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi8hAsbdwmHbN": "Tensor Swap",
    "TCMPhJdwDryooaGtiocG1u3xcYbRpiJzb283XfCZsDp": "Tensor cNFT",
    "hadeK9DLv9eA7ya5KCTqSvSvRZeJC3JgD5a9Y3CNbvu": "Hadeswap",
}

# Known staking programs
KNOWN_STAKING_PROGRAMS = {
    "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD": "Marinade Finance",
    "Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb": "Jito Staking",
    "Stake11111111111111111111111111111111111111": "Native Staking",
    "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy": "Stake Pool Program",
}

# Tax configuration
DEFAULT_COST_BASIS_METHOD = "fifo"  # fifo, lifo, specific_id
DUST_THRESHOLD_USD = 0.01
LONG_TERM_HOLDING_DAYS = 365

# Web server
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080
