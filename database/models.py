from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, Boolean, Numeric, JSON,
    ForeignKey, UniqueConstraint, Index, create_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(Text, unique=True, nullable=False, index=True)
    label = Column(Text, nullable=True)
    first_seen = Column(DateTime, nullable=True)
    last_scanned = Column(DateTime, nullable=True)
    total_sol_received = Column(Numeric(precision=20, scale=9), default=0)
    total_sol_sent = Column(Numeric(precision=20, scale=9), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="wallet", cascade="all, delete-orphan")
    token_transfers = relationship("TokenTransfer", back_populates="wallet", cascade="all, delete-orphan")
    sol_transfers = relationship("SolTransfer", back_populates="wallet", cascade="all, delete-orphan")
    swaps = relationship("Swap", back_populates="wallet", cascade="all, delete-orphan")
    cost_basis_lots = relationship("CostBasisLot", back_populates="wallet", cascade="all, delete-orphan")
    disposals = relationship("Disposal", back_populates="wallet", cascade="all, delete-orphan")
    wallet_interactions = relationship("WalletInteraction", back_populates="wallet", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signature = Column(Text, unique=True, nullable=False, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    block_time = Column(DateTime, nullable=True, index=True)
    slot = Column(Integer, nullable=True)
    fee_sol = Column(Numeric(precision=20, scale=9), default=0)
    fee_usd = Column(Numeric(precision=20, scale=6), nullable=True)
    tx_type = Column(Text, default="UNKNOWN")
    success = Column(Boolean, default=True)
    raw_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    wallet = relationship("Wallet", back_populates="transactions")
    token_transfers = relationship("TokenTransfer", back_populates="transaction", cascade="all, delete-orphan")
    sol_transfers = relationship("SolTransfer", back_populates="transaction", cascade="all, delete-orphan")
    swaps = relationship("Swap", back_populates="transaction", cascade="all, delete-orphan")


class TokenTransfer(Base):
    __tablename__ = "token_transfers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    mint_address = Column(Text, nullable=False, index=True)
    token_symbol = Column(Text, nullable=True)
    token_name = Column(Text, nullable=True)
    decimals = Column(Integer, nullable=True)
    amount = Column(Numeric(precision=30, scale=12), default=0)
    direction = Column(Text, nullable=False)  # 'in' or 'out'
    counterparty_address = Column(Text, nullable=True, index=True)
    usd_value_at_time = Column(Numeric(precision=20, scale=6), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship("Transaction", back_populates="token_transfers")
    wallet = relationship("Wallet", back_populates="token_transfers")


class SolTransfer(Base):
    __tablename__ = "sol_transfers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    amount_sol = Column(Numeric(precision=20, scale=9), default=0)
    direction = Column(Text, nullable=False)  # 'in' or 'out'
    counterparty_address = Column(Text, nullable=True, index=True)
    usd_value_at_time = Column(Numeric(precision=20, scale=6), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship("Transaction", back_populates="sol_transfers")
    wallet = relationship("Wallet", back_populates="sol_transfers")


class Swap(Base):
    __tablename__ = "swaps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    from_mint = Column(Text, nullable=True)
    from_symbol = Column(Text, nullable=True)
    from_amount = Column(Numeric(precision=30, scale=12), default=0)
    from_usd_value = Column(Numeric(precision=20, scale=6), nullable=True)
    to_mint = Column(Text, nullable=True)
    to_symbol = Column(Text, nullable=True)
    to_amount = Column(Numeric(precision=30, scale=12), default=0)
    to_usd_value = Column(Numeric(precision=20, scale=6), nullable=True)
    dex_program = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transaction = relationship("Transaction", back_populates="swaps")
    wallet = relationship("Wallet", back_populates="swaps")


class CostBasisLot(Base):
    __tablename__ = "cost_basis_lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    mint_address = Column(Text, nullable=False, index=True)
    token_symbol = Column(Text, nullable=True)
    acquisition_date = Column(DateTime, nullable=False)
    acquisition_amount = Column(Numeric(precision=30, scale=12), default=0)
    remaining_amount = Column(Numeric(precision=30, scale=12), default=0)
    cost_basis_per_unit_usd = Column(Numeric(precision=20, scale=10), default=0)
    total_cost_basis_usd = Column(Numeric(precision=20, scale=6), default=0)
    acquisition_tx_signature = Column(Text, nullable=True)
    acquisition_type = Column(Text, nullable=False)  # buy, swap_in, transfer_in, airdrop, staking_reward
    disposed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    wallet = relationship("Wallet", back_populates="cost_basis_lots")
    disposals = relationship("Disposal", back_populates="cost_basis_lot", cascade="all, delete-orphan")


class Disposal(Base):
    __tablename__ = "disposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    cost_basis_lot_id = Column(Integer, ForeignKey("cost_basis_lots.id"), nullable=False)
    mint_address = Column(Text, nullable=False)
    token_symbol = Column(Text, nullable=True)
    disposal_date = Column(DateTime, nullable=False)
    disposal_amount = Column(Numeric(precision=30, scale=12), default=0)
    proceeds_usd = Column(Numeric(precision=20, scale=6), default=0)
    cost_basis_usd = Column(Numeric(precision=20, scale=6), default=0)
    gain_loss_usd = Column(Numeric(precision=20, scale=6), default=0)
    holding_period = Column(Text, nullable=False)  # 'short' or 'long'
    disposal_type = Column(Text, nullable=False)  # sell, swap_out, transfer_out
    disposal_tx_signature = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    wallet = relationship("Wallet", back_populates="disposals")
    cost_basis_lot = relationship("CostBasisLot", back_populates="disposals")


class WalletInteraction(Base):
    __tablename__ = "wallet_interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    counterparty_address = Column(Text, nullable=False, index=True)
    counterparty_label = Column(Text, nullable=True)
    total_received_sol = Column(Numeric(precision=20, scale=9), default=0)
    total_sent_sol = Column(Numeric(precision=20, scale=9), default=0)
    total_received_usd = Column(Numeric(precision=20, scale=6), default=0)
    total_sent_usd = Column(Numeric(precision=20, scale=6), default=0)
    token_transfers_in = Column(Integer, default=0)
    token_transfers_out = Column(Integer, default=0)
    first_interaction = Column(DateTime, nullable=True)
    last_interaction = Column(DateTime, nullable=True)
    tx_count = Column(Integer, default=0)

    wallet = relationship("Wallet", back_populates="wallet_interactions")


class PriceCache(Base):
    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(Text, nullable=False)
    date = Column(Date, nullable=False)
    price_usd = Column(Numeric(precision=20, scale=10), nullable=True)
    source = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("mint_address", "date", name="uix_mint_date"),
    )
