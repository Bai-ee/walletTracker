"""Edge case tests: stablecoins, dust, own-wallet transfers, airdrops, idempotency."""
from decimal import Decimal

from processors.cost_basis import CostBasisEngine
from database.models import CostBasisLot, Disposal
from tests.conftest import (
    SOL_MINT, USDC_MINT, USDT_MINT, WBTC_MINT, BONK_MINT,
    make_tx, add_token_transfer, add_sol_transfer, add_swap,
    remaining_balance, lot_count, disposal_count,
)


# ────────────────────────────────────────────────────────────────────
# Stablecoin handling
# ────────────────────────────────────────────────────────────────────
class TestStablecoins:
    def test_no_lots_for_stablecoin_acquisitions(self, db_session, wallet):
        """Receiving USDC (stablecoin) should create 0 USDC lots."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="SOL_TRANSFER_IN", fee_sol=Decimal("0"))
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="1000", direction="in", usd=1000)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        assert lot_count(db_session, wallet, USDC_MINT) == 0
        assert stats["lots_created"] == 0

    def test_stablecoin_to_stablecoin_swap_no_lots(self, db_session, wallet):
        """USDC → USDT swap should create 0 lots and 0 disposals."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="SWAP")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx,
                           mint=USDT_MINT, symbol="USDT", amount="99.98", direction="in", usd=99.98)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=USDT_MINT, to_symbol="USDT", to_amount="99.98", to_usd=99.98)

        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        assert lot_count(db_session, wallet, USDC_MINT) == 0
        assert lot_count(db_session, wallet, USDT_MINT) == 0
        assert disposal_count(db_session, wallet, USDC_MINT) == 0

    def test_stablecoin_not_disposed_in_buy(self, db_session, wallet):
        """Using USDC to buy WBTC should not create a disposal for USDC."""
        tx = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=100)
        add_swap(db_session, wallet, tx,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=100)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        assert disposal_count(db_session, wallet, USDC_MINT) == 0


# ────────────────────────────────────────────────────────────────────
# Own-wallet transfers
# ────────────────────────────────────────────────────────────────────
class TestOwnWalletTransfers:
    def test_outbound_own_wallet_not_disposed(self, db_session, wallet):
        """Sending tokens to own wallet should NOT create a disposal."""
        own_addr = wallet.address

        # Acquire some BONK
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="50", direction="out", usd=50)
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=50)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="50", from_usd=50,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=50)

        # Transfer out to own wallet
        tx2 = make_tx(db_session, wallet, hour=2, tx_type="TOKEN_TRANSFER_OUT")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="500", direction="out",
                           counterparty=own_addr, usd=25)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet, tracked_wallets={own_addr})

        # Should NOT dispose — it's our own wallet
        assert disposal_count(db_session, wallet, BONK_MINT) == 0
        assert remaining_balance(db_session, wallet, BONK_MINT) == Decimal("1000")

    def test_inbound_own_wallet_labeled_transfer_in(self, db_session, wallet):
        """Receiving tokens from own wallet should be labeled transfer_in."""
        own_other = "OtherWalletAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

        tx = make_tx(db_session, wallet, hour=1, tx_type="TOKEN_TRANSFER_IN")
        add_token_transfer(db_session, wallet, tx,
                           mint=BONK_MINT, symbol="BONK", amount="500", direction="in",
                           counterparty=own_other, usd=25)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet, tracked_wallets={wallet.address, own_other})

        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == BONK_MINT
        ).first()
        assert lot.acquisition_type == "transfer_in"


# ────────────────────────────────────────────────────────────────────
# Airdrops and staking rewards
# ────────────────────────────────────────────────────────────────────
class TestAcquisitionTypes:
    def test_airdrop_labeled_correctly(self, db_session, wallet):
        tx = make_tx(db_session, wallet, hour=1, tx_type="AIRDROP")
        add_token_transfer(db_session, wallet, tx,
                           mint=BONK_MINT, symbol="BONK", amount="100000", direction="in", usd=10)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == BONK_MINT
        ).first()
        assert lot.acquisition_type == "airdrop"

    def test_staking_reward_labeled_correctly(self, db_session, wallet):
        tx = make_tx(db_session, wallet, hour=1, tx_type="STAKING_REWARD")
        add_sol_transfer(db_session, wallet, tx, amount="0.05", direction="in", usd=10)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        lot = db_session.query(CostBasisLot).filter(
            CostBasisLot.mint_address == SOL_MINT
        ).first()
        assert lot.acquisition_type == "staking_reward"


# ────────────────────────────────────────────────────────────────────
# Idempotency: running twice should give same results
# ────────────────────────────────────────────────────────────────────
class TestIdempotency:
    def test_reprocessing_gives_same_results(self, db_session, wallet):
        """Running process_wallet twice should clear and rebuild identically."""
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.001", direction="in", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=WBTC_MINT, to_symbol="WBTC", to_amount="0.001", to_usd=100)

        tx2 = make_tx(db_session, wallet, hour=2, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=WBTC_MINT, symbol="WBTC", amount="0.0005", direction="out", usd=60)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="60", direction="in", usd=60)
        add_swap(db_session, wallet, tx2,
                 from_mint=WBTC_MINT, from_symbol="WBTC", from_amount="0.0005", from_usd=60,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="60", to_usd=60)

        engine = CostBasisEngine(db_session, method="fifo")

        stats1 = engine.process_wallet(wallet)
        bal1 = remaining_balance(db_session, wallet, WBTC_MINT)

        stats2 = engine.process_wallet(wallet)
        bal2 = remaining_balance(db_session, wallet, WBTC_MINT)

        assert stats1 == stats2
        assert bal1 == bal2 == Decimal("0.0005")


# ────────────────────────────────────────────────────────────────────
# Empty wallet
# ────────────────────────────────────────────────────────────────────
class TestEmptyWallet:
    def test_no_transactions(self, db_session, wallet):
        """Processing a wallet with no transactions should succeed."""
        engine = CostBasisEngine(db_session, method="fifo")
        stats = engine.process_wallet(wallet)

        assert stats == {"lots_created": 0, "disposals_created": 0, "errors": 0}


# ────────────────────────────────────────────────────────────────────
# Holding period classification
# ────────────────────────────────────────────────────────────────────
class TestHoldingPeriod:
    def test_short_term(self, db_session, wallet):
        """Disposing within 365 days is short-term."""
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=100)

        # Sell 100 hours later (< 365 days)
        tx2 = make_tx(db_session, wallet, hour=100, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="out", usd=120)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="120", direction="in", usd=120)
        add_swap(db_session, wallet, tx2,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="1000", from_usd=120,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="120", to_usd=120)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        d = db_session.query(Disposal).filter(Disposal.mint_address == BONK_MINT).first()
        assert d.holding_period == "short"

    def test_long_term(self, db_session, wallet):
        """Disposing after 365+ days is long-term."""
        tx1 = make_tx(db_session, wallet, hour=1, tx_type="BUY")
        add_token_transfer(db_session, wallet, tx1,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="in", usd=100)
        add_token_transfer(db_session, wallet, tx1,
                           mint=USDC_MINT, symbol="USDC", amount="100", direction="out", usd=100)
        add_swap(db_session, wallet, tx1,
                 from_mint=USDC_MINT, from_symbol="USDC", from_amount="100", from_usd=100,
                 to_mint=BONK_MINT, to_symbol="BONK", to_amount="1000", to_usd=100)

        # Sell 400 days later (> 365 days)
        tx2 = make_tx(db_session, wallet, hour=400 * 24, tx_type="SELL")
        add_token_transfer(db_session, wallet, tx2,
                           mint=BONK_MINT, symbol="BONK", amount="1000", direction="out", usd=120)
        add_token_transfer(db_session, wallet, tx2,
                           mint=USDC_MINT, symbol="USDC", amount="120", direction="in", usd=120)
        add_swap(db_session, wallet, tx2,
                 from_mint=BONK_MINT, from_symbol="BONK", from_amount="1000", from_usd=120,
                 to_mint=USDC_MINT, to_symbol="USDC", to_amount="120", to_usd=120)

        engine = CostBasisEngine(db_session, method="fifo")
        engine.process_wallet(wallet)

        d = db_session.query(Disposal).filter(Disposal.mint_address == BONK_MINT).first()
        assert d.holding_period == "long"
