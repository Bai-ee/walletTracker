"""API and page routes for the web dashboard."""
import re
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import base58

from web.app import templates
from database.db import get_session
from database.models import Wallet, Transaction, TokenTransfer, SolTransfer, Swap
from fetchers.helius import HeliusFetcher
from fetchers.price_fetcher import PriceFetcher
from processors.normalizer import TransactionNormalizer
from processors.classifier import TransactionClassifier
from processors.wallet_graph import WalletGraphBuilder
from processors.cost_basis import CostBasisEngine
from processors.pnl_calculator import PnLCalculator
from reports.summary import SummaryReporter
from reports.form_8949 import Form8949Generator
from reports.csv_export import CSVExporter
from reports.pdf_report import PDFReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_solana_address(address: str) -> bool:
    """Validate a Solana address (base58, 32-44 chars)."""
    if not address or len(address) < 32 or len(address) > 44:
        return False
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception:
        return False


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


# ---- Page Routes ----

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard — wallet overview."""
    session = get_session()
    try:
        wallets = session.query(Wallet).all()
        wallet_data = []
        total_pnl = 0.0
        total_income = 0.0

        for w in wallets:
            tx_count = session.query(Transaction).filter(Transaction.wallet_id == w.id).count()
            pnl_calc = PnLCalculator(session)
            pnl = pnl_calc.wallet_summary(w)

            wallet_data.append({
                "wallet": w,
                "tx_count": tx_count,
                "pnl": pnl,
            })
            total_pnl += pnl["net_realized_pnl"]
            total_income += pnl["total_income"]

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "wallets": wallet_data,
            "total_pnl": total_pnl,
            "total_income": total_income,
        })
    finally:
        session.close()


@router.post("/wallet", response_class=HTMLResponse)
async def add_wallet(request: Request, address: str = Form(...), label: str = Form("")):
    """Add a new wallet."""
    address = address.strip()
    if not _validate_solana_address(address):
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "wallets": [],
            "total_pnl": 0,
            "total_income": 0,
            "error": "Invalid Solana address. Must be base58 encoded, 32-44 characters.",
        })

    session = get_session()
    try:
        existing = session.query(Wallet).filter(Wallet.address == address).first()
        if existing:
            return templates.TemplateResponse("dashboard.html", {
                "request": request,
                "wallets": [],
                "total_pnl": 0,
                "total_income": 0,
                "error": "Wallet already exists.",
            })

        wallet = Wallet(address=address, label=label or None)
        session.add(wallet)
        session.commit()

        # Redirect to wallet detail page
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/wallet/{address}", status_code=303)
    finally:
        session.close()


@router.get("/wallet/{address}", response_class=HTMLResponse)
async def wallet_detail(
    request: Request,
    address: str,
    page: int = Query(1, ge=1),
    tx_type: str = Query("", alias="type"),
    token: str = Query(""),
):
    """Per-wallet transaction detail page."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        per_page = 50
        query = session.query(Transaction).filter(Transaction.wallet_id == wallet.id)

        if tx_type:
            query = query.filter(Transaction.tx_type == tx_type)

        query = query.order_by(Transaction.block_time.desc())
        total_txs = query.count()
        total_pages = max(1, (total_txs + per_page - 1) // per_page)
        transactions = query.offset((page - 1) * per_page).limit(per_page).all()

        # Enrich transactions with transfer details
        tx_details = []
        for tx in transactions:
            sol_transfers = session.query(SolTransfer).filter(SolTransfer.transaction_id == tx.id).all()
            token_transfers = session.query(TokenTransfer).filter(TokenTransfer.transaction_id == tx.id).all()
            swaps = session.query(Swap).filter(Swap.transaction_id == tx.id).all()

            tx_details.append({
                "tx": tx,
                "sol_transfers": sol_transfers,
                "token_transfers": token_transfers,
                "swaps": swaps,
            })

        # PnL summary
        pnl_calc = PnLCalculator(session)
        pnl = pnl_calc.wallet_summary(wallet)
        token_pnl = pnl_calc.token_summary(wallet)

        # Holdings
        reporter = SummaryReporter(session)
        holdings = reporter.token_holdings(wallet)

        # Available tx types for filter
        type_counts = {}
        all_types = (
            session.query(Transaction.tx_type)
            .filter(Transaction.wallet_id == wallet.id)
            .all()
        )
        for (t,) in all_types:
            type_counts[t] = type_counts.get(t, 0) + 1

        return templates.TemplateResponse("wallet_detail.html", {
            "request": request,
            "wallet": wallet,
            "transactions": tx_details,
            "pnl": pnl,
            "token_pnl": token_pnl,
            "holdings": holdings,
            "page": page,
            "total_pages": total_pages,
            "total_txs": total_txs,
            "type_counts": type_counts,
            "current_type": tx_type,
        })
    finally:
        session.close()


@router.post("/wallet/{address}/scan")
async def scan_wallet(address: str):
    """Trigger a full scan for a wallet."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        # Fetch transactions
        fetcher = HeliusFetcher(session)
        new_txs = fetcher.fetch_all_transactions(wallet)

        # Normalize
        price_fetcher = PriceFetcher(session)
        normalizer = TransactionNormalizer(session, price_fetcher)
        normalized = normalizer.normalize_wallet_transactions(wallet, fetch_prices=True)

        # Classify
        all_wallet_addrs = set(
            w.address for w in session.query(Wallet).all()
        )
        classifier = TransactionClassifier(session, tracked_wallets=all_wallet_addrs)
        classified = classifier.classify_all(wallet)

        # Build wallet graph
        graph_builder = WalletGraphBuilder(session)
        counterparties = graph_builder.build_interactions(wallet)

        # Cost basis
        engine = CostBasisEngine(session, method="fifo")
        cb_stats = engine.process_wallet(wallet, tracked_wallets=all_wallet_addrs)

        return JSONResponse({
            "status": "success",
            "new_transactions": new_txs,
            "normalized": normalized,
            "classified": classified,
            "counterparties": counterparties,
            "cost_basis": cb_stats,
        })
    except Exception as e:
        logger.error(f"Scan error for {address}: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    finally:
        session.close()


@router.get("/wallet/{address}/graph", response_class=HTMLResponse)
async def wallet_graph(request: Request, address: str):
    """Wallet interaction visualization."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        graph_builder = WalletGraphBuilder(session)
        graph_data = graph_builder.get_graph_data(wallet, limit=50)

        return templates.TemplateResponse("wallet_graph.html", {
            "request": request,
            "wallet": wallet,
            "graph_data": json.dumps(graph_data, cls=DecimalEncoder),
        })
    finally:
        session.close()


@router.get("/tax-report", response_class=HTMLResponse)
async def tax_report(
    request: Request,
    year: int = Query(2025),
    method: str = Query("fifo"),
):
    """Tax report page."""
    session = get_session()
    try:
        wallets = session.query(Wallet).all()
        wallet_ids = [w.id for w in wallets]

        form_gen = Form8949Generator(session)
        form_data = form_gen.generate(wallet_ids=wallet_ids, year=year)

        reporter = SummaryReporter(session)
        global_summary = reporter.global_summary(year=year)

        return templates.TemplateResponse("tax_report.html", {
            "request": request,
            "year": year,
            "method": method,
            "form_data": form_data,
            "summary": global_summary,
        })
    finally:
        session.close()


# ---- API Export Routes ----

@router.get("/api/export/{format_type}")
async def export_data(
    format_type: str,
    year: int = Query(2025),
    wallet_id: Optional[int] = Query(None),
):
    """Export data in various formats."""
    session = get_session()
    try:
        wallets = session.query(Wallet).all()
        wallet_ids = [w.id for w in wallets] if not wallet_id else [wallet_id]

        if format_type == "csv_8949":
            exporter = CSVExporter(session)
            csv_data = exporter.export_form_8949(wallet_ids=wallet_ids, year=year)
            return StreamingResponse(
                iter([csv_data]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=form_8949_{year}.csv"},
            )

        elif format_type == "csv_turbotax":
            exporter = CSVExporter(session)
            csv_data = exporter.export_turbotax(wallet_ids=wallet_ids, year=year)
            return StreamingResponse(
                iter([csv_data]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=turbotax_{year}.csv"},
            )

        elif format_type == "csv_raw":
            if not wallet_id:
                raise HTTPException(status_code=400, detail="wallet_id required for raw export")
            exporter = CSVExporter(session)
            csv_data = exporter.export_raw_transactions(wallet_id)
            return StreamingResponse(
                iter([csv_data]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=transactions_raw.csv"},
            )

        elif format_type == "json":
            reporter = SummaryReporter(session)
            data = reporter.global_summary(year=year)
            return JSONResponse(content=json.loads(json.dumps(data, cls=DecimalEncoder)))

        elif format_type == "pdf":
            pdf_gen = PDFReportGenerator(session)
            pdf_bytes = pdf_gen.generate(year=year, wallet_ids=wallet_ids)
            return StreamingResponse(
                iter([pdf_bytes]),
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=tax_report_{year}.pdf"},
            )

        else:
            raise HTTPException(status_code=400, detail=f"Unknown format: {format_type}")
    finally:
        session.close()


@router.get("/api/wallet/{address}/summary")
async def api_wallet_summary(address: str, year: Optional[int] = Query(None)):
    """API endpoint for wallet summary data."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        pnl_calc = PnLCalculator(session)
        summary = pnl_calc.wallet_summary(wallet, year=year)
        return JSONResponse(content=json.loads(json.dumps(summary, cls=DecimalEncoder)))
    finally:
        session.close()
