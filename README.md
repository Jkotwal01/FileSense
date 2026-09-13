# FileSense

A local CLI-based file analysis and deduplication engine.

## Features
- Recursive directory scanning
- Multi-stage hashing pipeline (size → partial hash → full SHA-256)
- Exact duplicate detection
- SQLite persistent index for incremental rescans
- Concurrent hashing (configurable workers)
- Storage analysis (by extension, directory, largest files)
- Benchmarking engine
- Rich CLI output

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scan a directory
python main.py scan ./Downloads

# Find duplicates
python main.py duplicates ./Downloads

# Storage analysis
python main.py analyze ./Downloads

# Largest files
python main.py largest ./Downloads --top 10

# Full report
python main.py report ./Downloads

# Benchmark
python main.py benchmark ./Downloads

# Cleanup preview (dry-run)
python main.py clean ./Downloads --dry-run

# Index status
python main.py index status
```

## Architecture

```
CLI (Typer + Rich)
    └── ScanController
            ├── FileScanner      → discovers files, collects metadata
            ├── PersistentIndex  → SQLite cache (incremental scans)
            ├── HashManager      → PartialHasher + FullHasher (concurrent)
            ├── DuplicateDetector → multi-stage pipeline
            ├── StorageAnalyzer  → usage breakdown
            ├── ReportGenerator  → formatted output
            └── BenchmarkEngine  → naive vs multi-stage comparison
```

## Pipeline

```
Files → Size Grouping → Partial Hash → Full SHA-256 → Duplicate Groups
```
