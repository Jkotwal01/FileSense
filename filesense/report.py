"""ReportGenerator — formats and prints analysis results using Rich.

Uses Rich tables, panels, and progress styling for a premium terminal experience.
"""

import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from filesense.duplicate import DuplicateGroup
from filesense.metadata import FileMetadata
from filesense.scanner import ScanResult
from filesense.storage import StorageAnalyzer, _human_size

logger = logging.getLogger(__name__)
console = Console()


class ReportGenerator:
    """
    Formats FileSense analysis results for terminal output using Rich.

    All methods write directly to stdout via the shared Rich Console.
    """

    def __init__(self) -> None:
        self._analyzer = StorageAnalyzer()

    # ------------------------------------------------------------------
    # Full combined report
    # ------------------------------------------------------------------

    def full_report(
        self,
        scan_result: ScanResult,
        duplicate_groups: list[DuplicateGroup],
        root: str,
    ) -> None:
        """Print a complete FileSense report."""
        files = scan_result.files
        usage = self._analyzer.total_usage(files)
        dup_summary = self._analyzer.duplicate_summary(duplicate_groups)

        console.print()
        console.rule("[bold cyan]FILESENSE REPORT[/bold cyan]")
        console.print()

        # Summary panel
        summary = (
            f"[bold]Directory:[/bold]   {root}\n"
            f"[bold]Files:[/bold]       {usage['file_count']:,}\n"
            f"[bold]Directories:[/bold] {scan_result.directory_count:,}\n"
            f"[bold]Errors:[/bold]      {scan_result.error_count:,}\n"
            f"[bold]Total Storage:[/bold] [cyan]{usage['total_human']}[/cyan]\n"
            f"\n"
            f"[bold]Duplicate Groups:[/bold]   {dup_summary['group_count']:,}\n"
            f"[bold]Duplicate Files:[/bold]    {dup_summary['duplicate_file_count']:,}\n"
            f"[bold]Duplicate Storage:[/bold]  [yellow]{dup_summary['duplicate_human']}[/yellow]\n"
            f"[bold]Potential Savings:[/bold]  [green]{dup_summary['wasted_human']}[/green]"
        )
        console.print(Panel(summary, title="Summary", border_style="cyan"))

        # Largest files
        console.print()
        self.largest_files_report(files, top=5)

        # Extension breakdown
        console.print()
        self.extension_report(files, top=10)

        console.print()
        console.rule("[dim]End of Report[/dim]")

    # ------------------------------------------------------------------
    # Scan summary
    # ------------------------------------------------------------------

    def scan_summary(self, scan_result: ScanResult, elapsed: float) -> None:
        """Print a brief scan completion summary."""
        usage = self._analyzer.total_usage(scan_result.files)
        console.print()
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(style="cyan")
        table.add_row("Files discovered", f"{scan_result.file_count:,}")
        table.add_row("Directories", f"{scan_result.directory_count:,}")
        table.add_row("Total storage", usage["total_human"])
        table.add_row("Errors", str(scan_result.error_count))
        table.add_row("Elapsed", f"{elapsed:.2f}s")
        console.print(Panel(table, title="Scan Complete", border_style="green"))

    # ------------------------------------------------------------------
    # Duplicate report
    # ------------------------------------------------------------------

    def duplicate_report(self, groups: list[DuplicateGroup]) -> None:
        """Print all duplicate groups."""
        if not groups:
            console.print(Panel("[green]No duplicates found! ✓[/green]", border_style="green"))
            return

        dup_summary = self._analyzer.duplicate_summary(groups)
        console.print()
        console.print(
            Panel(
                f"[yellow]Found {len(groups):,} duplicate group(s)[/yellow]  |  "
                f"Potential savings: [green]{dup_summary['wasted_human']}[/green]",
                border_style="yellow",
            )
        )

        for i, group in enumerate(groups, 1):
            console.print()
            console.print(
                f"[bold yellow]Group #{i}[/bold yellow]  "
                f"[dim]{group.full_hash[:16]}...[/dim]  "
                f"[cyan]{_human_size(group.file_size)}[/cyan] x {group.file_count} copies  "
                f"-> [green]{_human_size(group.wasted_size)} recoverable[/green]"
            )
            orig = group.original_candidate
            console.print(f"  [bold green]KEEP[/bold green]    {orig.path}")
            for dup in group.duplicates:
                console.print(f"  [bold red]DUP[/bold red]     {dup.path}")

    # ------------------------------------------------------------------
    # Storage / extension report
    # ------------------------------------------------------------------

    def storage_report(self, files: list[FileMetadata]) -> None:
        """Print storage breakdown by category and directory."""
        usage = self._analyzer.total_usage(files)
        console.print(
            Panel(
                f"Total files: [cyan]{usage['file_count']:,}[/cyan]  |  "
                f"Total storage: [cyan]{usage['total_human']}[/cyan]",
                title="Storage Overview",
                border_style="cyan",
            )
        )

        # By category
        categories = self._analyzer.by_category(files)
        cat_table = Table(title="By Category", box=box.SIMPLE_HEAD)
        cat_table.add_column("Category", style="bold")
        cat_table.add_column("Files", justify="right")
        cat_table.add_column("Storage", justify="right", style="cyan")
        for row in categories:
            cat_table.add_row(row["category"], f"{row['count']:,}", row["human"])
        console.print(cat_table)

    def extension_report(self, files: list[FileMetadata], top: int = 15) -> None:
        """Print extension breakdown table."""
        exts = self._analyzer.by_extension(files)[:top]
        ext_table = Table(title=f"Top {top} Extensions by Storage", box=box.SIMPLE_HEAD)
        ext_table.add_column("Extension", style="bold")
        ext_table.add_column("Files", justify="right")
        ext_table.add_column("Storage", justify="right", style="cyan")
        for row in exts:
            ext_table.add_row(row["extension"], f"{row['count']:,}", row["human"])
        console.print(ext_table)

    # ------------------------------------------------------------------
    # Largest files
    # ------------------------------------------------------------------

    def largest_files_report(self, files: list[FileMetadata], top: int = 10) -> None:
        """Print the N largest files."""
        largest = self._analyzer.largest_files(files, top=top)
        table = Table(title=f"Top {top} Largest Files", box=box.SIMPLE_HEAD)
        table.add_column("#", justify="right", style="dim")
        table.add_column("File", no_wrap=False)
        table.add_column("Size", justify="right", style="cyan")
        for i, meta in enumerate(largest, 1):
            table.add_row(str(i), meta.path, meta.human_size())
        console.print(table)

    # ------------------------------------------------------------------
    # Dry-run cleanup preview
    # ------------------------------------------------------------------

    def cleanup_preview(self, groups: list[DuplicateGroup]) -> None:
        """Print a dry-run preview of what could be cleaned up."""
        if not groups:
            console.print("[green]Nothing to clean up.[/green]")
            return

        total_savings = sum(g.wasted_size for g in groups)
        console.print()
        console.print(
            Panel(
                f"[bold]Dry-run cleanup preview[/bold]\n"
                f"This will NOT delete any files.\n"
                f"Potential recovery: [green]{_human_size(total_savings)}[/green]",
                border_style="yellow",
                title="[!] Dry Run",
            )
        )

        for i, group in enumerate(groups, 1):
            orig = group.original_candidate
            console.print(f"\n[bold]Group #{i}[/bold] — {_human_size(group.file_size)} each")
            console.print(f"  [bold green]KEEP[/bold green]    {orig.path}")
            for dup in group.duplicates:
                console.print(f"  [bold red]DELETE[/bold red]  {dup.path}")

    # ------------------------------------------------------------------
    # Index status
    # ------------------------------------------------------------------

    def index_status_report(self, stats: dict) -> None:
        """Print the current index status."""
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(style="cyan")
        table.add_row("Database", stats["db_path"])
        table.add_row("Indexed files", f"{stats['total_files']:,}")
        table.add_row("Hashed files", f"{stats['hashed_files']:,}")
        table.add_row("Total indexed size", _human_size(stats["total_bytes"]))
        console.print(Panel(table, title="Index Status", border_style="blue"))
