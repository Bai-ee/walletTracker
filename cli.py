"""CLI entry point for the Solana Wallet Scanner."""
import logging
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
import base58

from database.db import init_db, get_session
from database.models import Wallet, Transaction

console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _validate_address(address: str) -> bool:
    if not address or len(address) < 32 or len(address) > 44:
        return False
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception:
        return False


@click.group()
def cli():
    """Solana Wallet Scanner & Tax Analyzer"""
    init_db()


@cli.command("add-wallet")
@click.argument("address")
@click.option("--label", default="", help="Human-readable label for this wallet")
def add_wallet(address: str, label: str):
    """Add a wallet to track."""
    if not _validate_address(address):
        console.print("[red]Invalid Solana address.[/red]")
        sys.exit(1)

    session = get_session()
    try:
        existing = session.query(Wallet).filter(Wallet.address == address).first()
        if existing:
            console.print(f"[yellow]Wallet already exists:[/yellow] {existing.label or existing.address}")
            return

        wallet = Wallet(address=address, label=label or None)
        session.add(wallet)
        session.commit()
        console.print(f"[green]Added wallet:[/green] {label or address[:12]}...")
    finally:
        session.close()


@cli.command("scan")
@click.argument("address")
def scan(address: str):
    """Scan a wallet — fetch all transactions and process them."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            console.print("[red]Wallet not found. Add it first with add-wallet.[/red]")
            sys.exit(1)

        from fetchers.helius import HeliusFetcher
        from fetchers.price_fetcher import PriceFetcher
        from processors.normalizer import TransactionNormalizer
        from processors.classifier import TransactionClassifier
        from processors.wallet_graph import WalletGraphBuilder
        from processors.cost_basis import CostBasisEngine

        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            # Fetch
            task = progress.add_task("Fetching transactions from Helius...", total=None)
            fetcher = HeliusFetcher(session)
            new_txs = fetcher.fetch_all_transactions(wallet)
            progress.update(task, description=f"Fetched {new_txs} new transactions")

            # Normalize
            progress.update(task, description="Normalizing transactions...")
            price_fetcher = PriceFetcher(session)
            normalizer = TransactionNormalizer(session, price_fetcher)
            normalized = normalizer.normalize_wallet_transactions(wallet, fetch_prices=True)
            progress.update(task, description=f"Normalized {normalized} transactions")

            # Classify
            progress.update(task, description="Classifying transactions...")
            all_addrs = set(w.address for w in session.query(Wallet).all())
            classifier = TransactionClassifier(session, tracked_wallets=all_addrs)
            classified = classifier.classify_all(wallet)
            progress.update(task, description=f"Classified {classified} transactions")

            # Wallet graph
            progress.update(task, description="Building wallet interaction graph...")
            graph_builder = WalletGraphBuilder(session)
            cp_count = graph_builder.build_interactions(wallet)

            # Cost basis
            progress.update(task, description="Computing cost basis (FIFO)...")
            engine = CostBasisEngine(session, method="fifo")
            cb_stats = engine.process_wallet(wallet, tracked_wallets=all_addrs)

            progress.update(task, description="[green]Scan complete!")

        console.print(f"\n[bold green]Scan Results for {wallet.label or wallet.address[:12]}...[/bold green]")
        console.print(f"  New transactions: {new_txs}")
        console.print(f"  Normalized: {normalized}")
        console.print(f"  Classified: {classified}")
        console.print(f"  Counterparties: {cp_count}")
        console.print(f"  Cost basis lots: {cb_stats['lots_created']}")
        console.print(f"  Disposals: {cb_stats['disposals_created']}")
    finally:
        session.close()


@cli.command("scan-all")
def scan_all():
    """Scan all tracked wallets."""
    session = get_session()
    try:
        wallets = session.query(Wallet).all()
        if not wallets:
            console.print("[yellow]No wallets to scan. Add wallets first.[/yellow]")
            return

        for wallet in wallets:
            console.print(f"\n[bold]Scanning {wallet.label or wallet.address[:12]}...[/bold]")
            # Invoke scan logic directly
            from fetchers.helius import HeliusFetcher
            from fetchers.price_fetcher import PriceFetcher
            from processors.normalizer import TransactionNormalizer
            from processors.classifier import TransactionClassifier
            from processors.wallet_graph import WalletGraphBuilder
            from processors.cost_basis import CostBasisEngine

            fetcher = HeliusFetcher(session)
            new_txs = fetcher.fetch_all_transactions(wallet)
            console.print(f"  Fetched {new_txs} new transactions")

            price_fetcher = PriceFetcher(session)
            normalizer = TransactionNormalizer(session, price_fetcher)
            normalizer.normalize_wallet_transactions(wallet)

            all_addrs = set(w.address for w in session.query(Wallet).all())
            classifier = TransactionClassifier(session, tracked_wallets=all_addrs)
            classifier.classify_all(wallet)

            graph_builder = WalletGraphBuilder(session)
            graph_builder.build_interactions(wallet)

            engine = CostBasisEngine(session, method="fifo")
            engine.process_wallet(wallet, tracked_wallets=all_addrs)

            console.print(f"  [green]Done![/green]")
    finally:
        session.close()


@cli.command("summary")
@click.argument("address")
def summary(address: str):
    """Show wallet summary and PnL."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            console.print("[red]Wallet not found.[/red]")
            sys.exit(1)

        from processors.pnl_calculator import PnLCalculator
        from reports.summary import SummaryReporter

        pnl_calc = PnLCalculator(session)
        pnl = pnl_calc.wallet_summary(wallet)

        reporter = SummaryReporter(session)
        overview = reporter.wallet_overview(wallet)

        console.print(f"\n[bold]{wallet.label or wallet.address}[/bold]")
        console.print(f"Address: {wallet.address}")
        console.print(f"First seen: {overview['first_seen'] or 'N/A'}")
        console.print(f"Total transactions: {overview['total_transactions']}")
        console.print(f"Unique tokens: {overview['unique_tokens']}")
        console.print(f"Total swaps: {overview['total_swaps']}")
        console.print(f"SOL in: {overview['total_sol_in']:.4f}  |  SOL out: {overview['total_sol_out']:.4f}")

        # PnL table
        table = Table(title="P&L Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Amount (USD)", justify="right")

        table.add_row("Short-Term Gains", f"${pnl['short_term_gains']:,.2f}")
        table.add_row("Short-Term Losses", f"(${abs(pnl['short_term_losses']):,.2f})")
        table.add_row("Long-Term Gains", f"${pnl['long_term_gains']:,.2f}")
        table.add_row("Long-Term Losses", f"(${abs(pnl['long_term_losses']):,.2f})")
        table.add_row("Net Realized P&L", f"${pnl['net_realized_pnl']:,.2f}", style="bold")
        table.add_row("Total Income", f"${pnl['total_income']:,.2f}")
        table.add_row("Total Fees", f"${pnl['total_fees']:,.2f}")

        console.print(table)

        # Transaction type breakdown
        if overview["type_breakdown"]:
            type_table = Table(title="Transaction Types")
            type_table.add_column("Type", style="cyan")
            type_table.add_column("Count", justify="right")
            for tx_type, count in sorted(overview["type_breakdown"].items(), key=lambda x: x[1], reverse=True):
                type_table.add_row(tx_type, str(count))
            console.print(type_table)
    finally:
        session.close()


@cli.command("transactions")
@click.argument("address")
@click.option("--type", "tx_type", default="", help="Filter by transaction type")
@click.option("--token", default="", help="Filter by token symbol")
@click.option("--limit", default=50, help="Max transactions to show")
def transactions(address: str, tx_type: str, token: str, limit: int):
    """Show transactions for a wallet."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            console.print("[red]Wallet not found.[/red]")
            sys.exit(1)

        query = session.query(Transaction).filter(Transaction.wallet_id == wallet.id)
        if tx_type:
            query = query.filter(Transaction.tx_type == tx_type.upper())
        query = query.order_by(Transaction.block_time.desc()).limit(limit)
        txs = query.all()

        table = Table(title=f"Transactions for {wallet.label or wallet.address[:12]}...")
        table.add_column("Date", style="dim")
        table.add_column("Type")
        table.add_column("Fee (SOL)", justify="right")
        table.add_column("Signature", style="dim")

        for tx in txs:
            date_str = tx.block_time.strftime("%Y-%m-%d %H:%M") if tx.block_time else "N/A"
            type_style = "green" if tx.tx_type in ("BUY", "SOL_TRANSFER_IN", "STAKING_REWARD") else \
                         "red" if tx.tx_type in ("SELL", "SOL_TRANSFER_OUT") else \
                         "blue" if tx.tx_type == "SWAP" else "yellow"
            table.add_row(
                date_str,
                f"[{type_style}]{tx.tx_type}[/{type_style}]",
                f"{float(tx.fee_sol):.6f}" if tx.fee_sol else "0",
                tx.signature[:16] + "...",
            )

        console.print(table)
        console.print(f"Showing {len(txs)} of {session.query(Transaction).filter(Transaction.wallet_id == wallet.id).count()} transactions")
    finally:
        session.close()


@cli.command("tax-report")
@click.option("--year", default=2025, help="Tax year")
@click.option("--method", default="fifo", type=click.Choice(["fifo", "lifo"]))
@click.option("--export", "export_fmt", default="", type=click.Choice(["", "csv", "turbotax", "pdf"]))
def tax_report(year: int, method: str, export_fmt: str):
    """Generate tax report."""
    session = get_session()
    try:
        from reports.form_8949 import Form8949Generator
        from reports.csv_export import CSVExporter
        from reports.pdf_report import PDFReportGenerator
        from reports.summary import SummaryReporter

        wallets = session.query(Wallet).all()
        wallet_ids = [w.id for w in wallets]

        if export_fmt == "csv":
            exporter = CSVExporter(session)
            csv_data = exporter.export_form_8949(wallet_ids=wallet_ids, year=year)
            filename = f"form_8949_{year}.csv"
            with open(filename, "w") as f:
                f.write(csv_data)
            console.print(f"[green]Exported to {filename}[/green]")
            return

        if export_fmt == "turbotax":
            exporter = CSVExporter(session)
            csv_data = exporter.export_turbotax(wallet_ids=wallet_ids, year=year)
            filename = f"turbotax_{year}.csv"
            with open(filename, "w") as f:
                f.write(csv_data)
            console.print(f"[green]Exported to {filename}[/green]")
            return

        if export_fmt == "pdf":
            gen = PDFReportGenerator(session)
            pdf_bytes = gen.generate(year=year, wallet_ids=wallet_ids)
            filename = f"tax_report_{year}.pdf"
            with open(filename, "wb") as f:
                f.write(pdf_bytes)
            console.print(f"[green]Exported to {filename}[/green]")
            return

        # Display in terminal
        form_gen = Form8949Generator(session)
        form_data = form_gen.generate(wallet_ids=wallet_ids, year=year)

        reporter = SummaryReporter(session)
        global_summary = reporter.global_summary(year=year)
        totals = global_summary["totals"]

        console.print(f"\n[bold]Tax Report — {year} ({method.upper()})[/bold]\n")

        table = Table(title="Tax Summary")
        table.add_column("Category", style="cyan")
        table.add_column("Amount (USD)", justify="right")
        table.add_row("Short-Term Gains", f"${totals['short_term_gains']:,.2f}")
        table.add_row("Short-Term Losses", f"(${abs(totals['short_term_losses']):,.2f})")
        table.add_row("Long-Term Gains", f"${totals['long_term_gains']:,.2f}")
        table.add_row("Long-Term Losses", f"(${abs(totals['long_term_losses']):,.2f})")
        table.add_row("Net Realized P&L", f"${totals['net_realized_pnl']:,.2f}", style="bold")
        table.add_row("Total Income", f"${totals['total_income']:,.2f}")
        table.add_row("Total Fees", f"${totals['total_fees']:,.2f}")
        console.print(table)

        console.print(f"\nShort-term disposals: {form_data['short_term_totals']['count']}")
        console.print(f"Long-term disposals: {form_data['long_term_totals']['count']}")
        console.print(f"\nUse --export csv/turbotax/pdf to export.")
    finally:
        session.close()


@cli.command("counterparties")
@click.argument("address")
@click.option("--sort-by", default="volume", type=click.Choice(["volume", "tx_count"]))
@click.option("--limit", default=20)
def counterparties(address: str, sort_by: str, limit: int):
    """Show counterparty analysis for a wallet."""
    session = get_session()
    try:
        wallet = session.query(Wallet).filter(Wallet.address == address).first()
        if not wallet:
            console.print("[red]Wallet not found.[/red]")
            sys.exit(1)

        from reports.summary import SummaryReporter
        reporter = SummaryReporter(session)
        cps = reporter.counterparty_summary(wallet, limit=limit)

        if sort_by == "tx_count":
            cps.sort(key=lambda x: x["tx_count"], reverse=True)

        table = Table(title=f"Top Counterparties for {wallet.label or wallet.address[:12]}...")
        table.add_column("Address", style="dim")
        table.add_column("Label")
        table.add_column("Received (USD)", justify="right", style="green")
        table.add_column("Sent (USD)", justify="right", style="red")
        table.add_column("Txs", justify="right")

        for cp in cps:
            table.add_row(
                cp["address"][:16] + "...",
                cp["label"] or "",
                f"${cp['total_received_usd']:,.2f}",
                f"${cp['total_sent_usd']:,.2f}",
                str(cp["tx_count"]),
            )

        console.print(table)
    finally:
        session.close()


@cli.command("serve")
@click.option("--port", default=8080, help="Port to serve on")
@click.option("--host", default="0.0.0.0")
def serve(port: int, host: str):
    """Launch the web dashboard."""
    import uvicorn
    console.print(f"[bold green]Starting web dashboard at http://{host}:{port}[/bold green]")
    uvicorn.run("web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    cli()
