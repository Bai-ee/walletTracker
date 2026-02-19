"""Integration test: rebuild cost basis from the real DB and verify against on-chain.

Requires:
  - A populated solana_scanner.db (from a prior scan)
  - Network access to Helius RPC

Run with:  pytest tests/test_cost_basis_integrity.py -v -s
Skip with: pytest -m "not integration"
"""
import os
import pytest
import requests
from decimal import Decimal
from sqlalchemy import func

from database.db import init_db, get_session
from database.models import Wallet, CostBasisLot, TokenTransfer
from processors.cost_basis import CostBasisEngine
from config import STABLECOIN_MINTS, HELIUS_API_KEY, HELIUS_RPC_URL

SOL_MINT = "So11111111111111111111111111111111111111112"

needs_network = pytest.mark.skipif(
    not HELIUS_API_KEY,
    reason="HELIUS_API_KEY not set — skipping on-chain integrity test",
)
integration = pytest.mark.integration


def _get_onchain_balances(address: str) -> dict[str, float]:
    """Fetch all non-zero token balances + SOL from on-chain."""
    sol_resp = requests.post(HELIUS_RPC_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address],
    }, timeout=15)
    balances = {SOL_MINT: sol_resp.json()["result"]["value"] / 1e9}

    for program in [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    ]:
        resp = requests.post(HELIUS_RPC_URL, json={
            "jsonrpc": "2.0", "id": 2,
            "method": "getTokenAccountsByOwner",
            "params": [address, {"programId": program}, {"encoding": "jsonParsed"}],
        }, timeout=15)
        for acct in resp.json()["result"]["value"]:
            info = acct["account"]["data"]["parsed"]["info"]
            mint = info["mint"]
            amt = float(info["tokenAmount"]["uiAmountString"])
            if amt > 0:
                balances[mint] = amt
    return balances


@integration
@needs_network
def test_all_token_balances_match_onchain():
    """
    Rebuild cost basis from the real DB and assert every non-stablecoin,
    non-SOL token balance matches on-chain exactly.
    """
    init_db()
    session = get_session()
    try:
        wallet = session.query(Wallet).first()
        if not wallet:
            pytest.skip("No wallet in database")

        # Rebuild
        all_addrs = set(w.address for w in session.query(Wallet).all())
        engine = CostBasisEngine(session, method="fifo")
        stats = engine.process_wallet(wallet, tracked_wallets=all_addrs)
        assert stats["errors"] == 0, f"Cost basis rebuild had {stats['errors']} errors"

        # Computed balances
        lot_balances = (
            session.query(
                CostBasisLot.mint_address,
                func.sum(CostBasisLot.remaining_amount),
            )
            .filter(
                CostBasisLot.wallet_id == wallet.id,
                CostBasisLot.remaining_amount > 0,
            )
            .group_by(CostBasisLot.mint_address)
            .all()
        )
        computed = {mint: float(rem) for mint, rem in lot_balances}

        # On-chain balances
        onchain = _get_onchain_balances(wallet.address)

        # Compare every non-stablecoin token (skip SOL — known limitation)
        all_mints = set(list(computed.keys()) + list(onchain.keys()))
        failures = []

        for mint in sorted(all_mints):
            if mint in STABLECOIN_MINTS or mint == SOL_MINT:
                continue

            c = computed.get(mint, 0.0)
            o = onchain.get(mint, 0.0)

            if abs(c - o) > 1e-6:
                # Look up symbol for readable output
                tt = session.query(TokenTransfer).filter(
                    TokenTransfer.mint_address == mint
                ).first()
                sym = tt.token_symbol if tt else mint[:12]
                failures.append(
                    f"  {sym}: computed={c:.9f}  on-chain={o:.9f}  diff={c - o:+.9f}"
                )

        assert not failures, (
            f"{len(failures)} token balance(s) do not match on-chain:\n"
            + "\n".join(failures)
        )
    finally:
        session.close()


@integration
@needs_network
def test_sol_balance_within_tolerance():
    """SOL balance should be within 0.01 SOL of on-chain (rent refund tolerance)."""
    init_db()
    session = get_session()
    try:
        wallet = session.query(Wallet).first()
        if not wallet:
            pytest.skip("No wallet in database")

        all_addrs = set(w.address for w in session.query(Wallet).all())
        engine = CostBasisEngine(session, method="fifo")
        engine.process_wallet(wallet, tracked_wallets=all_addrs)

        computed = float(
            session.query(func.sum(CostBasisLot.remaining_amount))
            .filter(
                CostBasisLot.wallet_id == wallet.id,
                CostBasisLot.mint_address == SOL_MINT,
                CostBasisLot.remaining_amount > 0,
            )
            .scalar() or 0
        )

        onchain = _get_onchain_balances(wallet.address).get(SOL_MINT, 0.0)
        diff = abs(computed - onchain)

        assert diff < 0.01, (
            f"SOL balance off by {diff:.6f}: computed={computed:.9f}, on-chain={onchain:.9f}"
        )
    finally:
        session.close()
