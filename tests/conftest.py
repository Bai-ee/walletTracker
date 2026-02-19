"""Shared fixtures for cost basis engine tests.

Uses an in-memory SQLite database — no network calls, no side effects.
"""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base, Wallet, Transaction, TokenTransfer, SolTransfer, Swap,
    CostBasisLot, Disposal,
)

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
WBTC_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"
BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"

T0 = datetime(2025, 1, 1, 0, 0, 0)


def ts(hours: int = 0) -> datetime:
    """Helper: T0 + hours."""
    return T0 + timedelta(hours=hours)


@pytest.fixture
def db_session():
    """In-memory SQLite session, tables created fresh each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def wallet(db_session):
    """A default wallet."""
    w = Wallet(address="WalletAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", label="Test")
    db_session.add(w)
    db_session.flush()
    return w


# ── Builder helpers ──────────────────────────────────────────────────────

_sig_counter = 0


def make_tx(session, wallet, *, hour=0, tx_type="UNKNOWN", fee_sol=Decimal("0.000005"), fee_usd=None):
    """Create a Transaction and return it."""
    global _sig_counter
    _sig_counter += 1
    tx = Transaction(
        signature=f"sig_{_sig_counter:06d}",
        wallet_id=wallet.id,
        block_time=ts(hour),
        slot=hour * 1000,
        fee_sol=fee_sol,
        fee_usd=fee_usd,
        tx_type=tx_type,
        success=True,
    )
    session.add(tx)
    session.flush()
    return tx


def add_token_transfer(session, wallet, tx, *, mint, symbol, amount, direction,
                       counterparty=None, usd=None):
    tt = TokenTransfer(
        transaction_id=tx.id,
        wallet_id=wallet.id,
        mint_address=mint,
        token_symbol=symbol,
        amount=Decimal(str(amount)),
        direction=direction,
        counterparty_address=counterparty,
        usd_value_at_time=Decimal(str(usd)) if usd is not None else None,
    )
    session.add(tt)
    session.flush()
    return tt


def add_sol_transfer(session, wallet, tx, *, amount, direction,
                     counterparty=None, usd=None):
    st = SolTransfer(
        transaction_id=tx.id,
        wallet_id=wallet.id,
        amount_sol=Decimal(str(amount)),
        direction=direction,
        counterparty_address=counterparty,
        usd_value_at_time=Decimal(str(usd)) if usd is not None else None,
    )
    session.add(st)
    session.flush()
    return st


def add_swap(session, wallet, tx, *,
             from_mint, from_symbol, from_amount, from_usd=None,
             to_mint, to_symbol, to_amount, to_usd=None):
    s = Swap(
        transaction_id=tx.id,
        wallet_id=wallet.id,
        from_mint=from_mint,
        from_symbol=from_symbol,
        from_amount=Decimal(str(from_amount)),
        from_usd_value=Decimal(str(from_usd)) if from_usd is not None else None,
        to_mint=to_mint,
        to_symbol=to_symbol,
        to_amount=Decimal(str(to_amount)),
        to_usd_value=Decimal(str(to_usd)) if to_usd is not None else None,
    )
    session.add(s)
    session.flush()
    return s


def remaining_balance(session, wallet, mint) -> Decimal:
    """Sum of remaining_amount for a mint across all lots."""
    from sqlalchemy import func
    result = (
        session.query(func.sum(CostBasisLot.remaining_amount))
        .filter(
            CostBasisLot.wallet_id == wallet.id,
            CostBasisLot.mint_address == mint,
            CostBasisLot.remaining_amount > 0,
        )
        .scalar()
    )
    return result or Decimal("0")


def lot_count(session, wallet, mint) -> int:
    return (
        session.query(CostBasisLot)
        .filter(CostBasisLot.wallet_id == wallet.id, CostBasisLot.mint_address == mint)
        .count()
    )


def disposal_count(session, wallet, mint) -> int:
    return (
        session.query(Disposal)
        .filter(Disposal.wallet_id == wallet.id, Disposal.mint_address == mint)
        .count()
    )
