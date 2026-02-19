"""Core cost basis engine tests — buys, sells, FIFO/LIFO ordering, gain/loss."""
from decimal import Decimal

from processors.cost_basis import CostBasisEngine
from database.models import CostBasisLot, Disposal
from tests.conftest import (
    SOL_MINT, USDC_MINT, WBTC_MINT, BONK_MINT,
    make_tx, add_token_transfer, add_sol_transfer, add_swap,
    remaining_balance, lot_count, disposal_count,
)


# ────────────────────────────────────────────────────────────────────
# Simple buy (USDC → Token)
# ────────────────────────────────────────────────────────────────────
class TestSimpleBuy:
    def test_creates_lot_for_acquired_token(self, db_session, wallet):
        """Buying WBTC with USDC should create 1 lot for WBTC, 0 for USDC."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=100)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=100)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        assert stats["errors"] == 0
        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0.001")
        assert lot_count(db_session, wallet, USDC_MINT) == 0  # no stablecoin lots

    def test_cost_basis_from_swap_usd(self, db_session, wallet):
        """Cost basis per unit should be from_usd / to_amount."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="500", direction="out", usd=500)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.005", direction="in", usd=500)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="500", from_usd=500,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.005", to_usd=500)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == WBTC_MINT
        ).first()
        assert lot.cost_basis_per_unit_usd == Decimal("500") / Decimal("0.005")
        assert lot.total_cost_basis_usd == Decimal("500")
        assert lot.acquisition_type == "buy"


# ────────────────────────────────────────────────────────────────────
# Simple sell (Token → USDC)
# ────────────────────────────────────────────────────────────────────
class TestSimpleSell:
    def test_sell_creates_disposal(self, db_session, wallet):
        """Buy then sell should leave 0 balance and create a disposal."""
        # Buy
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=100)

        # Sell
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="out", usd=120)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="120", direction="in", usd=120)
        add_swap(db_session, wallet, tx2,
                 from_mint=WBTC_MINT, from_symbol="WBTC", from_amount="0.001", from_usd=120,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="120", to_usd=120)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0")
        assert disposal_count(db_session, wallet, WBTC_MINT) == 1

    def test_gain_loss_calculation(self, db_session, wallet):
        """Gain = proceeds - cost basis."""
        # Buy at $100
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=100)

        # Sell at $150
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="out", usd=150)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="150", direction="in", usd=150)
        add_swap(db_session, wallet, tx2,
                 from_mint=WBTC_MINT, from_symbol="WBTC", from_amount="0.001", from_usd=150,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="150", to_usd=150)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        d = db_session.query(Disposal).filter(Disposal.mint_address == WBTC_MINT).first()
        assert d.proceeds_usd == Decimal("150")
        assert d.cost_basis_usd == Decimal("100")
        assert d.gain_loss_usd == Decimal("50")


# ────────────────────────────────────────────────────────────────────
# FIFO ordering
# ────────────────────────────────────────────────────────────────────
class TestFIFO:
    def test_sells_oldest_lot_first(self, db_session, wallet):
        """FIFO: selling should consume the earlier (cheaper) lot first."""
        # Buy #1 at $100/unit (hour 1)
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=100)

        # Buy #2 at $200/unit (hour 2)
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=200)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="200", direction="out", usd=200)
        add_swap(db_session, wallet, tx2,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="200", from_usd=200,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=200)

        # Sell 1000 at $150 (hour 3)
        tx3 = make_tx(db_session, wallet, hour=3, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx3,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="out", usd=150)
        add_token_transfer(db_session, wallet, tx3,
                           mint=USDC_MINT, symbol="USDC", amount="150", direction="in", usd=150)
        add_swap(db_session, wallet, tx3,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="1000", from_usd=150,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="150", to_usd=150)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        d = db_session.query(Disposal).filter(Disposal.mint_address == BONK_MINT).first()
        # FIFO: consumed lot #1 at $0.10/unit → cost = 1000 * 0.10 = $100
        assert d.cost_basis_usd == Decimal("100")
        assert d.gain_loss_usd == Decimal("50")
        assert remaining_balance(db_session, wallet, BONK_MINT) == Decimal("1000")


# ────────────────────────────────────────────────────────────────────
# LIFO ordering
# ────────────────────────────────────────────────────────────────────
class TestLIFO:
    def test_sells_newest_lot_first(self, db_session, wallet):
        """LIFO: selling should consume the later (more expensive) lot first."""
        # Buy #1 at $100/unit (hour 1)
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=100)

        # Buy #2 at $200/unit (hour 2)
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=200)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="200", direction="out", usd=200)
        add_swap(db_session, wallet, tx2,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="200", from_usd=200,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=200)

        # Sell 1000 at $150 (hour 3)
        tx3 = make_tx(db_session, wallet, hour=3, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx3,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="out", usd=150)
        add_token_transfer(db_session, wallet, tx3,
                           mint=USDC_MINT, symbol="USDC", amount="150", direction="in", usd=150)
        add_swap(db_session, wallet, tx3,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="1000", from_usd=150,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="150", to_usd=150)

        engine = CostBasisEngine(db_session, method="lifo")
        engine.process_wallet(wallet)

        d = db_session.query(Disposal).filter(Disposal.mint_address == BONK_MINT).first()
        # LIFO: consumed lot #2 at $0.20/unit → cost = 1000 * 0.20 = $200
        assert d.cost_basis_usd == Decimal("200")
        assert d.gain_loss_usd == Decimal("-50")
        assert remaining_balance(db_session, wallet, BONK_MINT) == Decimal("1000")


# ────────────────────────────────────────────────────────────────────
# Partial sell across multiple lots
# ────────────────────────────────────────────────────────────────────
class TestPartialSell:
    def test_partial_sell_across_lots(self, db_session, wallet):
        """Selling more than one lot should span both lots (FIFO)."""
        # Lot 1: 500 BONK at $0.10
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="500", direction="in", usd=50)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="50", direction="out", usd=50)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="50", from_usd=50,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="500", to_usd=50)

        # Lot 2: 500 BONK at $0.20
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="500", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx2,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="500", to_usd=100)

        # Sell 700 BONK
        tx3 = make_tx(db_session, wallet, hour=3, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx3,
                           mint=BONK_MINT, symbol="BONK", amount="700", direction="out", usd=140)
        add_token_transfer(db_session, wallet, tx3,
                           mint=USDC_MINT, symbol="USDC", amount="140", direction="in", usd=140)
        add_swap(db_session, wallet, tx3,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="700", from_usd=140,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="140", to_usd=140)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # Should have 2 disposals (from lot 1 and lot 2)
        assert disposal_count(db_session, wallet, BONK_MINT) == 2
        # 300 remaining (500 + 500 - 700)
        assert remaining_balance(db_session, wallet, BONK_MINT) == Decimal("300")


# ────────────────────────────────────────────────────────────────────
# SOL fee deduction
# ────────────────────────────────────────────────────────────────────
class TestSOLFees:
    def test_fees_reduce_sol_balance(self, db_session, wallet):
        """Transaction fees should reduce the computed SOL balance."""
        # Receive 1 SOL
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="SOL_TRANSFER_IN", fee_sol=Decimal("0.000005"))
        add_sol_transfer(db_session, wallet, tx1,
                         amount="1.0", direction="in", usd=200)

        # A tx that just has a fee
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="UNKNOWN", fee_sol=Decimal("0.1"))

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        # SOL balance should be: 1.0 - 0.000005 - 0.1 = 0.899995
        expected = Decimal("1.0") - Decimal("0.000005") - Decimal("0.1")
        assert remaining_balance(db_session, wallet, SOL_MINT) == expected


# ────────────────────────────────────────────────────────────────────
# Sell with no prior lots (synthetic zero-cost lot)
# ────────────────────────────────────────────────────────────────────
class TestZeroCostBasis:
    def test_disposal_without_prior_lot_creates_synthetic(self, db_session, wallet):
        """Selling a token we never 'bought' should create a zero-cost synthetic lot."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="SELL", fee_sol=Decimal("0"))
        add_token_transfer(db_session, wallet, tx,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="out", usd=50)
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="50", direction="in", usd=50)
        add_swap(db_session, wallet, tx,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="1000", from_usd=50,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="50", to_usd=50)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        # Should create 1 synthetic lot + 1 disposal (for BONK only, no SOL fee)
        assert stats["lots_created"] == 1
        assert stats["disposals_created"] == 1

        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == BONK_MINT
        ).first()
        assert lot.acquisition_type == "unknown"
        assert lot.total_cost_basis_usd == Decimal("0")
        assert lot.remaining_amount == Decimal("0")


# ────────────────────────────────────────────────────────────────────
# Token-to-token swap (non-stablecoin)
# ────────────────────────────────────────────────────────────────────
class TestTokenToTokenSwap:
    def test_swap_creates_disposal_and_lot(self, db_session, wallet):
        """Swapping BONK → WBTC should dispose BONK and acquire WBTC."""
        # First, acquire some BONK
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="10000", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="10000", to_usd=100)

        # Swap BONK → WBTC
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SWAP")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="10000", direction="out", usd=120)
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=120)
        add_swap(db_session, wallet, tx2,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="10000", from_usd=120,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=120)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        assert remaining_balance(db_session, wallet, BONK_MINT) == Decimal("0")
        assert remaining_balance(db_session, wallet, WBTC_MINT) == Decimal("0.001")
        assert disposal_count(db_session, wallet, BONK_MINT) == 1

        # WBTC lot cost basis should come from what we gave up
        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == WBTC_MINT
        ).first()
        assert lot.acquisition_type == "swap_in"
