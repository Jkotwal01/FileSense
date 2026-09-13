"""FileSense CLI — entry point for all commands.

Commands:
  scan        Scan a directory and index files
  duplicates  Find and report duplicate files
  analyze     Show storage breakdown
  largest     Show largest files
  report      Generate a complete report
  benchmark   Compare naive vs multi-stage hashing
  clean       Preview files that could be deleted (dry-run)
  index       Show persistent index status
"""

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich import box

from filesense.benchmark import BenchmarkEngine
from filesense.duplicate import DuplicateDetector
from filesense.index import PersistentIndex
from filesense.logging_config import setup_logging
from filesense.report import ReportGenerator
from filesense.scanner import FileScanner
from filesense.storage import StorageAnalyzer, _human_size

app = typer.Typer(
    name="filesense",
    help="FileSense — Local File Analysis & Deduplication Engine",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console(highlight=False, force_terminal=True)


def _resolve_path(path: str) -> Path:
    """Resolve and validate the target directory path."""
    p = Path(path).resolve()
    if not p.exists():
        console.print(f"[red]Error:[/red] Path not found: {p}")
        raise typer.Exit(code=1)
    if not p.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {p}")
        raise typer.Exit(code=1)
    return p


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@app.command()
def scan(
    path: str = typer.Argument(..., help="Directory to scan"),
    workers: int = typer.Option(4, "--workers", "-w", help="Concurrent hashing workers"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Follow symbolic links"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """Scan a directory, collect metadata, detect duplicates, and update the index."""
    setup_logging(verbose)
    root = _resolve_path(path)

    console.print(f"\n[bold cyan]FileSense[/bold cyan] scanning: [cyan]{root}[/cyan]")

    # Scan
    scanner = FileScanner(follow_symlinks=follow_symlinks)
    t_start = time.perf_counter()

    with console.status("[bold green]Scanning directory tree...[/bold green]"):
        scan_result = scanner.scan(root)

    elapsed_scan = time.perf_counter() - t_start
    console.print(
        f"  [green][OK][/green] {scan_result.file_count:,} files  "
        f"[dim]{scan_result.directory_count:,} dirs  "
        f"{scan_result.error_count} errors  "
        f"{elapsed_scan:.2f}s[/dim]"
    )

    # Load index & resolve incremental hashes
    index = PersistentIndex()
    files = scan_result.files

    with console.status("[bold green]Checking index for cached hashes...[/bold green]"):
        cached = 0
        for meta in files:
            before = meta.full_hash
            index.load_hashes(meta)
            if meta.full_hash and not before:
                cached += 1

    if cached:
        console.print(f"  [green][CACHED][/green] {cached:,} files with cached hashes (incremental scan)")

    # Detect duplicates
    with console.status("[bold yellow]Running duplicate detection pipeline...[/bold yellow]"):
        detector = DuplicateDetector(workers=workers)
        groups = detector.detect(files)

    # Persist all metadata to index
    with console.status("[bold blue]Updating persistent index...[/bold blue]"):
        for meta in files:
            index.upsert(meta)
    index.close()

    elapsed_total = time.perf_counter() - t_start

    # Report
    reporter = ReportGenerator()
    reporter.scan_summary(scan_result, elapsed_total)

    if groups:
        console.print(
            f"\n[yellow]Found {len(groups):,} duplicate group(s)[/yellow]  "
            f"-> [green]{_human_size(sum(g.wasted_size for g in groups))} recoverable[/green]"
        )
        console.print("[dim]Run [bold]filesense duplicates[/bold] for full details.[/dim]")
    else:
        console.print("\n[green]No duplicates found.[/green]")


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------

@app.command()
def duplicates(
    path: str = typer.Argument(..., help="Directory to analyze"),
    workers: int = typer.Option(4, "--workers", "-w", help="Concurrent hashing workers"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find and display all duplicate file groups in a directory."""
    setup_logging(verbose)
    root = _resolve_path(path)

    console.print(f"\n[bold cyan]FileSense[/bold cyan] duplicates: [cyan]{root}[/cyan]\n")

    scanner = FileScanner()
    with console.status("Scanning..."):
        scan_result = scanner.scan(root)

    index = PersistentIndex()
    for meta in scan_result.files:
        index.load_hashes(meta)

    with console.status("Detecting duplicates..."):
        detector = DuplicateDetector(workers=workers)
        groups = detector.detect(scan_result.files)

    for meta in scan_result.files:
        index.upsert(meta)
    index.close()

    ReportGenerator().duplicate_report(groups)


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    path: str = typer.Argument(..., help="Directory to analyze"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show storage breakdown by category and extension."""
    setup_logging(verbose)
    root = _resolve_path(path)

    console.print(f"\n[bold cyan]FileSense[/bold cyan] analyze: [cyan]{root}[/cyan]\n")

    scanner = FileScanner()
    with console.status("Scanning..."):
        scan_result = scanner.scan(root)

    reporter = ReportGenerator()
    reporter.storage_report(scan_result.files)
    console.print()
    reporter.extension_report(scan_result.files, top=15)


# ---------------------------------------------------------------------------
# largest
# ---------------------------------------------------------------------------

@app.command()
def largest(
    path: str = typer.Argument(..., help="Directory to analyze"),
    top: int = typer.Option(10, "--top", "-n", help="Number of files to show"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the N largest files in a directory."""
    setup_logging(verbose)
    root = _resolve_path(path)

    console.print(f"\n[bold cyan]FileSense[/bold cyan] largest: [cyan]{root}[/cyan]\n")

    scanner = FileScanner()
    with console.status("Scanning..."):
        scan_result = scanner.scan(root)

    ReportGenerator().largest_files_report(scan_result.files, top=top)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report(
    path: str = typer.Argument(..., help="Directory to report on"),
    workers: int = typer.Option(4, "--workers", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate a full FileSense report (scan + duplicates + storage analysis)."""
    setup_logging(verbose)
    root = _resolve_path(path)

    scanner = FileScanner()
    with console.status(f"Scanning {root}..."):
        scan_result = scanner.scan(root)

    index = PersistentIndex()
    for meta in scan_result.files:
        index.load_hashes(meta)

    with console.status("Detecting duplicates..."):
        detector = DuplicateDetector(workers=workers)
        groups = detector.detect(scan_result.files)

    for meta in scan_result.files:
        index.upsert(meta)
    index.close()

    ReportGenerator().full_report(scan_result, groups, str(root))


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------

@app.command()
def benchmark(
    path: str = typer.Argument(..., help="Directory to benchmark on"),
    workers: str = typer.Option("1,2,4", "--workers", help="Comma-separated worker counts"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Benchmark naive full hashing vs multi-stage hashing pipeline."""
    setup_logging(verbose)
    root = _resolve_path(path)

    workers_list = [int(w.strip()) for w in workers.split(",") if w.strip().isdigit()]

    console.print(f"\n[bold cyan]FileSense Benchmark[/bold cyan]: [cyan]{root}[/cyan]")
    console.print(f"Worker configs: {workers_list}\n")

    engine = BenchmarkEngine(workers_list=workers_list)
    with console.status("Running benchmarks (this may take a while)..."):
        results = engine.run(str(root))

    if not results:
        console.print("[yellow]No files to benchmark.[/yellow]")
        return

    table = Table(title="Benchmark Results", box=box.ROUNDED)
    table.add_column("Method", style="bold")
    table.add_column("Workers", justify="center")
    table.add_column("Time (s)", justify="right", style="cyan")
    table.add_column("Disk Read", justify="right")
    table.add_column("Hash Ops", justify="right")
    table.add_column("Dup Groups", justify="right", style="yellow")
    table.add_column("Peak RAM", justify="right")
    table.add_column("Throughput", justify="right", style="green")

    for r in results:
        d = r.as_dict()
        table.add_row(
            d["method"],
            str(d["workers"]),
            f"{d['elapsed_s']:.3f}",
            d["bytes_read_human"],
            f"{d['hash_ops']:,}",
            str(d["dup_groups"]),
            d["peak_mem_human"],
            f"{d['throughput_mbs']:.1f} MB/s",
        )

    console.print(table)

    # Show improvement vs naive
    if len(results) >= 2:
        naive = results[0]
        best_multi = min(results[1:], key=lambda r: r.elapsed_seconds)
        time_saved = naive.elapsed_seconds - best_multi.elapsed_seconds
        io_saved = naive.bytes_read - best_multi.bytes_read
        pct_faster = (time_saved / naive.elapsed_seconds * 100) if naive.elapsed_seconds else 0
        console.print(
            Panel(
                f"Best multi-stage vs naive:\n"
                f"  Time saved:   [cyan]{time_saved:.3f}s[/cyan] ({pct_faster:.1f}% faster)\n"
                f"  I/O saved:    [cyan]{_human_size(max(0, io_saved))}[/cyan]",
                title="Improvement",
                border_style="green",
            )
        )


# ---------------------------------------------------------------------------
# clean (dry-run)
# ---------------------------------------------------------------------------

@app.command()
def clean(
    path: str = typer.Argument(..., help="Directory to preview cleanup for"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Preview only (default: dry-run)"),
    workers: int = typer.Option(4, "--workers", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Preview (or perform) cleanup of duplicate files. Default is dry-run."""
    setup_logging(verbose)
    root = _resolve_path(path)

    scanner = FileScanner()
    with console.status("Scanning..."):
        scan_result = scanner.scan(root)

    index = PersistentIndex()
    for meta in scan_result.files:
        index.load_hashes(meta)

    with console.status("Detecting duplicates..."):
        detector = DuplicateDetector(workers=workers)
        groups = detector.detect(scan_result.files)

    for meta in scan_result.files:
        index.upsert(meta)
    index.close()

    reporter = ReportGenerator()
    reporter.cleanup_preview(groups)

    if not groups:
        return

    if not dry_run:
        total_savings = sum(g.wasted_size for g in groups)
        total_files = sum(len(g.duplicates) for g in groups)
        console.print()
        console.print(
            Panel(
                f"[bold red][!] WARNING - ACTUAL DELETION[/bold red]\n"
                f"You are about to delete [bold]{total_files}[/bold] file(s).\n"
                f"Potential recovery: [green]{_human_size(total_savings)}[/green]\n\n"
                f"[bold]This action cannot be undone.[/bold]",
                border_style="red",
            )
        )
        confirmed = Confirm.ask("Continue?", default=False)
        if confirmed:
            deleted = 0
            errors = 0
            for group in groups:
                for dup in group.duplicates:
                    try:
                        Path(dup.path).unlink()
                        console.print(f"  [red]Deleted:[/red] {dup.path}")
                        deleted += 1
                    except OSError as exc:
                        console.print(f"  [red]Error:[/red] {dup.path} — {exc}")
                        errors += 1
            console.print(f"\n[green]Deleted {deleted} file(s).[/green] Errors: {errors}")
        else:
            console.print("[yellow]Cancelled. No files were deleted.[/yellow]")
    else:
        console.print("\n[dim]Run with [bold]--no-dry-run[/bold] to perform actual deletion.[/dim]")


# ---------------------------------------------------------------------------
# index (subcommand group)
# ---------------------------------------------------------------------------

index_app = typer.Typer(help="Manage the persistent file index.")
app.add_typer(index_app, name="index")


@index_app.command("status")
def index_status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show statistics about the persistent file index."""
    setup_logging(verbose)
    idx = PersistentIndex()
    stats = idx.stats()
    idx.close()
    ReportGenerator().index_status_report(stats)
