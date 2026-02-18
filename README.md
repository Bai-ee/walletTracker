# Solana Wallet Scanner & Tax Analyzer

A local Python application that scans Solana wallet addresses, retrieves all historical transaction data, classifies transactions, tracks wallet-to-wallet flows, calculates cost basis and PnL, and produces IRS-compliant tax reports.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure API keys:**
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys:
   # - HELIUS_API_KEY (required) — get from https://helius.dev
   # - COINGECKO_API_KEY (optional) — for historical prices
   ```

3. **Initialize database:**
   ```bash
   python -c "from database.db import init_db; init_db()"
   ```

## Usage

### Web Dashboard
```bash
python main.py
# or
python cli.py serve --port 8080
```
Open http://localhost:8080 in your browser.

### CLI Commands
```bash
# Add a wallet
python cli.py add-wallet <address> --label "My Wallet"

# Scan a wallet (fetch + process + classify + cost basis)
python cli.py scan <address>

# Scan all tracked wallets
python cli.py scan-all

# Show wallet summary
python cli.py summary <address>

# List transactions
python cli.py transactions <address> --type swap --limit 50

# Generate tax report
python cli.py tax-report --year 2025 --method fifo
python cli.py tax-report --year 2025 --export csv
python cli.py tax-report --year 2025 --export turbotax
python cli.py tax-report --year 2025 --export pdf

# Counterparty analysis
python cli.py counterparties <address> --sort-by volume
```

## Architecture

- **Database:** SQLite with WAL mode (local, zero-config)
- **Data Source:** Helius Enhanced Transaction History API (parsed transactions)
- **Prices:** CoinGecko historical + Jupiter current prices
- **Cost Basis:** FIFO (default) or LIFO
- **Export:** CSV (Form 8949, TurboTax), JSON, PDF
- **Web:** FastAPI + Jinja2 + Tailwind CSS

## Limitations

- DeFi yield farming LP positions require manual review
- Leveraged positions (Drift, Mango) flagged for manual review
- Cross-chain bridges show Solana side only
- Compressed NFT pricing is approximate
- Not a substitute for professional tax advice
