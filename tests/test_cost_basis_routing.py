"""Regression tests for DEX routing artifacts and double-counting bugs.

These tests reproduce the exact patterns that were found in the real wallet
and broke earlier versions of the cost basis engine.
"""
from decimal import Decimal

from processors.cost_basis import CostBasisEngine
from database.models import CostBasisLot, Disposal
from tests.conftest import (
    SOL_MINT, USDC_MINT, USDT_MINT, WBTC_MINT, BONK_MINT, MSOL_MINT,
    make_tx, add_token_transfer, add_sol_transfer, add_swap,
    remaining_balance, lot_count, disposal_count,
)


# ────────────────────────────────────────────────────────────────────
# DEX routing artifact: token in + token out in same swap tx (net = 0)
# Pattern: USDC → WBTC swap where WBTC routes through the wallet
#   TokenTransfers: USDC out, WBTC in, WBTC out  (WBTC in == WBTC out)
#   Swap record:    USDC → WBTC
#   On-chain net:   WBTC = 0 (routing pass-through, e.g. DCA fill)
# ────────────────────────────────────────────────────────────────────
class TestBuyWithRoutingArtifact:
    def test_routing_artifact_nets_to_zero(self, db_session, wallet):
        """WBTC in + WBTC out (equal) should produce 0 WBTC balance."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="10", direction="out", usd=10)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.000114", direction="in", usd=10)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.000114", direction="out", usd=10)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="10", from_usd=10,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.000114", to_usd=10)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        # Net WBTC = 0, so NO lot and NO disposal for WBTC
        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0")
        assert lot_count(db_session, wallet, WBTC_MINT) == 0
        assert disposal_count(db_session, wallet, WBTC_MINT) == 0

    def test_dca_fill_then_claim(self, db_session, wallet):
        """DCA fill (routing, net=0) followed by DCA claim (transfer_in) = correct balance."""
        # DCA fill — WBTC routes through, net = 0
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="10", direction="out", usd=10)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.000114", direction="in", usd=10)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.000114", direction="out", usd=10)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="10", from_usd=10,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.000114", to_usd=10)

        # DCA claim — WBTC arrives via transfer_in
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.000114", direction="in", usd=10)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0.000114")
        assert lot_count(db_session, wallet, WBTC_MINT) == 1


# ────────────────────────────────────────────────────────────────────
# Sell routing artifact: token out + token in in same sell tx
# Pattern: mSOL → USDC sell where mSOL routes through the wallet
#   TokenTransfers: mSOL out, USDC in, mSOL in  (mSOL in == mSOL out)
#   Swap record:    mSOL → USDC
#   On-chain net mSOL: 0
# ────────────────────────────────────────────────────────────────────
class TestSellWithRoutingArtifact:
    def test_sell_routing_nets_to_zero_for_sold_token(self, db_session, wallet):
        """mSOL out + mSOL in (equal) in a sell tx should produce net 0 for mSOL."""
        # First acquire mSOL
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=MSOL_MINT, symbol="mSOL", amount="0.5", direction="in", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=MSOL_MINT, to_symbol="mSOL", to_amount="0.5", to_usd=100)

        # Sell with routing artifact (mSOL out AND mSOL in, equal amounts)
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=MSOL_MINT, symbol="mSOL", amount="0.5", direction="out", usd=110)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="110", direction="in", usd=110)
        add_token_transfer(db_session, wallet, tx2,
                           mint=MSOL_MINT, symbol="mSOL", amount="0.5", direction="in", usd=110)
        add_swap(db_session, wallet, tx2,
                 from_mint=MSOL_MINT, from_symbol="mSOL", from_amount="0.5", from_usd=110,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="110", to_usd=110)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # mSOL net in tx2 = 0 (routing), so balance should still be 0.5 from tx1
        assert remaining_balance(db_session, wallet, MSOL_MINT) == Decimal("0.5")


# ────────────────────────────────────────────────────────────────────
# wSOL double-counting: SOL appears as BOTH nativeTransfer AND tokenTransfer
# ────────────────────────────────────────────────────────────────────
class TestWSOLDoubleCounting:
    def test_sol_token_transfer_is_ignored(self, db_session, wallet):
        """SOL arriving as both SolTransfer and TokenTransfer (wSOL) should count once."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="SWAP", fee_sol=Decimal("0.00002"))
        # SolTransfer (native): SOL in
        add_sol_transfer(db_session, wallet, tx, amount="0.765", direction="in", usd=150)
        add_sol_transfer(db_session, wallet, tx, amount="0.004", direction="out", usd=0.8)
        # TokenTransfer (wSOL): same SOL in — should be ignored
        add_token_transfer(db_session, wallet, tx,
                           mint=SOL_MINT, symbol="SOL", amount="0.763", direction="in", usd=149)
        # Also a stablecoin swap record
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx,
                           mint=USDT_MINT, symbol="USDT", amount="100", direction="in", usd=100)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=USDT_MINT, to_symbol="USDT", to_amount="100", to_usd=100)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # SOL balance should be from SolTransfers ONLY: 0.765 - 0.004 - 0.00002 = 0.76098
        expected = Decimal("0.765") - Decimal("0.004") - Decimal("0.00002")
        assert remaining_balance(db_session, wallet, SOL_MINT) == expected


# ────────────────────────────────────────────────────────────────────
# Multi-token swap tx: 3 tokens move but swap only captures 2
# Pattern: mSOL → USDC sell that also involves BTC out
#   TokenTransfers: mSOL out, USDC in, BTC out, mSOL in
#   Swap record:    mSOL → USDC
#   On-chain: mSOL net=0 (routing), BTC net=-0.2, USDC net=+109
# ────────────────────────────────────────────────────────────────────
class TestMultiTokenSwap:
    def test_extra_token_out_not_in_swap_creates_disposal(self, db_session, wallet):
        """A token going out that's not in the Swap record should still create a disposal."""
        btc_mint = "BTCMINTADDRESS111111111111111111111111111111"

        # Acquire BTC and mSOL first
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="200", direction="out", usd=200)
        add_token_transfer(db_session, wallet, tx1,
                           mint=btc_mint, symbol="BTC", amount="0.5", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=MSOL_MINT, symbol="mSOL", amount="1.0", direction="in", usd=100)
        # (simplified — normally separate swaps, but for test purposes)

        # Multi-token sell tx
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=MSOL_MINT, symbol="mSOL", amount="0.6", direction="out", usd=110)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="109", direction="in", usd=109)
        add_token_transfer(db_session, wallet, tx2,
                           mint=btc_mint, symbol="BTC", amount="0.2", direction="out", usd=40)
        add_token_transfer(db_session, wallet, tx2,
                           mint=MSOL_MINT, symbol="mSOL", amount="0.6", direction="in", usd=110)
        add_swap(db_session, wallet, tx2,
                 from_mint=MSOL_MINT, from_symbol="mSOL", from_amount="0.6", from_usd=110,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="109", to_usd=109)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # mSOL net in tx2 = 0 (routing) → balance stays at 1.0
        assert remaining_balance(db_session, wallet, MSOL_MINT) == Decimal("1.0")
        # BTC net in tx2 = -0.2 → disposal created, balance = 0.5 - 0.2 = 0.3
        assert remaining_balance(db_session, wallet, btc_mint) == Decimal("0.3")
        assert disposal_count(db_session, wallet, btc_mint) == 1


# ────────────────────────────────────────────────────────────────────
# Extra acquisition not in swap record (AMZNx pattern)
# Pattern: swap captures first in-transfer but there's a second one
#   TokenTransfers: AMZNx in (0.177), AMZNx in (0.236)
#   Swap record:    USDC → AMZNx (0.177)
#   On-chain: AMZNx = 0.413 (both transfers)
# ────────────────────────────────────────────────────────────────────
class TestExtraAcquisition:
    def test_second_in_transfer_beyond_swap_creates_lot(self, db_session, wallet):
        """Two in-transfers in same tx should both be captured, even if swap only covers one."""
        amnz_mint = "AMZNxMINTADDRESS11111111111111111111111111"

        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="43", direction="out", usd=43)
        add_token_transfer(db_session, wallet, tx,
                           mint=amnz_mint, symbol="AMZNx", amount="0.177", direction="in", usd=21.5)
        add_token_transfer(db_session, wallet, tx,
                           mint=amnz_mint, symbol="AMZNx", amount="0.236", direction="in", usd=21.5)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="43", from_usd=43,
                 to_mint=amnz_mint, to_symbol="AMZNx", to_amount="0.177", to_usd=43)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # Net AMZNx in = 0.177 + 0.236 = 0.413
        assert remaining_balance(db_session, wallet, amnz_mint) == Decimal("0.413")


# ────────────────────────────────────────────────────────────────────
# Non-routing buy (no artifact): USDC out, WBTC in (only)
# ────────────────────────────────────────────────────────────────────
class TestNonRoutingBuy:
    def test_clean_buy_works_normally(self, db_session, wallet):
        """A swap with no routing artifact should create a normal lot."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="1000", direction="out", usd=1000)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.01", direction="in", usd=1000)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="1000", from_usd=1000,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.01", to_usd=1000)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0.01")
        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == WBTC_MINT
        ).first()
        assert lot.total_cost_basis_usd == Decimal("1000")


# ────────────────────────────────────────────────────────────────────
# Mixed: some routing, some clean buys, then sell
# Full lifecycle matching real wallet patterns
# ────────────────────────────────────────────────────────────────────
class TestMixedRoutingAndClean:
    def test_balance_matches_net_after_mixed_txs(self, db_session, wallet):
        """Mix of DCA fills (routing) + direct buys + sells = correct balance."""
        # DCA fill #1 (routing: WBTC in + out, net=0)
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="10", direction="out", usd=10)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.0001", direction="in", usd=10)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.0001", direction="out", usd=10)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="10", from_usd=10,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.0001", to_usd=10)

        # DCA claim (transfer_in)
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.0001", direction="in", usd=10)

        # Direct buy (clean, no routing)
        tx3 = make_tx(db_session, wallet, hour=3, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx3,
                           mint=USDC_MINT, symbol="USDC", amount="1000", direction="out", usd=1000)
        add_token_transfer(db_session, wallet, tx3,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.01", direction="in", usd=1000)
        add_swap(db_session, wallet, tx3,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="1000", from_usd=1000,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.01", to_usd=1000)

        # Sell half
        tx4 = make_tx(db_session, wallet, hour=4, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx4,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.005", direction="out", usd=600)
        add_token_transfer(db_session, wallet, tx4,
                           mint=USDC_MINT, symbol="USDC", amount="600", direction="in", usd=600)
        add_swap(db_session, wallet, tx4,
                 from_mint=WBTC_MINT, from_symbol="WBTC", from_amount="0.005", from_usd=600,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="600", to_usd=600)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        # Net WBTC: 0 (routing) + 0.0001 (claim) + 0.01 (buy) - 0.005 (sell) = 0.0051
        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0.0051")
        assert stats["errors"] == 0
