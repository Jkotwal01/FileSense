# FileSense

A local CLI-based **file analysis and deduplication engine** built in Python 3.x.

Efficiently identifies duplicate files using a multi-stage hashing pipeline
(size → partial hash → full SHA-256), maintains a persistent SQLite index for
incremental rescans, and provides storage breakdown reports.

---

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Running Unit Tests](#running-unit-tests)
- [Using the CLI](#using-the-cli)
  - [scan](#1-scan)
  - [duplicates](#2-duplicates)
  - [analyze](#3-analyze)
  - [largest](#4-largest)
  - [report](#5-report)
  - [benchmark](#6-benchmark)
  - [clean](#7-clean-dry-run)
  - [index status](#8-index-status)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Pipeline](#pipeline)

---

## Requirements

- Python **3.12+** (tested on 3.13.1)
- Windows / Linux / macOS

---

## Installation

```bash
# 1. Clone or open the project folder
cd "d:\My Code\FileSense"

# 2. Install dependencies
pip install -r requirements.txt
```

**Dependencies installed:**

| Package  | Purpose                        |
|----------|--------------------------------|
| `typer`  | CLI framework                  |
| `rich`   | Coloured terminal output       |
| `pytest` | Unit testing                   |
| `psutil` | Memory usage in benchmarks     |

---

## Running Unit Tests

### Run all tests

```bash
python -X utf8 -m pytest tests/ -v
```

Expected output:

```
collected 55 items

tests/test_duplicate.py::TestDuplicateDetector::test_finds_exact_duplicates PASSED
tests/test_duplicate.py::TestDuplicateDetector::test_different_sizes_not_duplicates PASSED
...
tests/test_storage.py::TestStorageAnalyzer::test_duplicate_summary_empty PASSED

55 passed in 0.83s
```

### Run a specific test file

```bash
python -X utf8 -m pytest tests/test_hasher.py -v
python -X utf8 -m pytest tests/test_scanner.py -v
python -X utf8 -m pytest tests/test_duplicate.py -v
python -X utf8 -m pytest tests/test_storage.py -v
```

### Run a specific test by name

```bash
python -X utf8 -m pytest tests/test_duplicate.py::TestDuplicateDetector::test_finds_exact_duplicates -v
```

### Run with short traceback on failure

```bash
python -X utf8 -m pytest tests/ -v --tb=short
```

---

## Using the CLI

> **Windows note:** Always run with `python -X utf8 main.py` to enable UTF-8
> output and avoid encoding errors in the terminal.

---

### 1. `scan`

Scan a directory, collect metadata, detect duplicates, and update the index.

```bash
python -X utf8 main.py scan <path>
```

**Examples:**

```bash
# Scan current directory with 4 workers (default)
python -X utf8 main.py scan .

# Scan Downloads folder with 8 workers
python -X utf8 main.py scan "C:\Users\YourName\Downloads" --workers 8

# Scan and follow symbolic links
python -X utf8 main.py scan ./data --follow-symlinks

# Verbose mode (shows DEBUG logs in terminal)
python -X utf8 main.py scan . --verbose
```

**Sample output:**

```
FileSense scanning: D:\My Code\FileSense

  [OK] 88 files  52 dirs  0 errors  0.03s
  [CACHED] 4 files with cached hashes (incremental scan)

┌─────────────────── Scan Complete ───────────────────┐
│  Files discovered   88                              │
│  Directories        52                              │
│  Total storage      209.1 KB                        │
│  Errors             0                               │
│  Elapsed            0.26s                           │
└─────────────────────────────────────────────────────┘

Found 2 duplicate group(s)  -> 1.6 KB recoverable
Run filesense duplicates for full details.
```

---

### 2. `duplicates`

Find and display all duplicate file groups.

```bash
python -X utf8 main.py duplicates <path>
```

**Examples:**

```bash
python -X utf8 main.py duplicates .
python -X utf8 main.py duplicates "C:\Users\YourName\Documents"
python -X utf8 main.py duplicates ./Downloads --workers 4
```

**Sample output:**

```
Found 2 duplicate group(s)  |  Potential savings: 1.6 KB

Group #1  593c17bf1f6e3c7b...  1.6 KB x 2 copies  -> 1.6 KB recoverable
  KEEP    D:\Projects\report.pdf
  DUP     D:\Backup\report.pdf

Group #2  e3b0c44298fc1c14...  0.0 B x 2 copies  -> 0.0 B recoverable
  KEEP    D:\Projects\empty.txt
  DUP     D:\Downloads\empty_copy.txt
```

---

### 3. `analyze`

Show storage breakdown by category and extension.

```bash
python -X utf8 main.py analyze <path>
```

**Examples:**

```bash
python -X utf8 main.py analyze .
python -X utf8 main.py analyze "C:\Users\YourName\Downloads"
```

**Sample output:**

```
┌──────────────── Storage Overview ───────────────────┐
│ Total files: 115  |  Total storage: 350.2 KB        │
└─────────────────────────────────────────────────────┘

   By Category
  ─────────────────────────
  Category    Files  Storage
  Other         95   267.0 KB
  Code          17    81.2 KB
  Documents      3     1.9 KB

  Top 15 Extensions by Storage
  ──────────────────────────────
  Extension   Files   Storage
  .pyc          16   174.5 KB
  .py           17    81.2 KB
  .md            2     1.9 KB
```

---

### 4. `largest`

Display the N largest files in a directory.

```bash
python -X utf8 main.py largest <path> --top <N>
```

**Examples:**

```bash
# Top 10 (default)
python -X utf8 main.py largest .

# Top 5
python -X utf8 main.py largest "C:\Users\YourName\Downloads" --top 5

# Top 20
python -X utf8 main.py largest . --top 20
```

**Sample output:**

```
  Top 5 Largest Files
  ───────────────────────────────────────────────
  #   File                                  Size
  1   D:\Data\dataset.zip               18.2 GB
  2   D:\Videos\movie.mkv               12.4 GB
  3   D:\Backup\backup.iso               9.7 GB
  4   D:\Projects\build.tar              4.1 GB
  5   D:\Downloads\installer.exe         1.2 GB
```

---

### 5. `report`

Generate a complete combined report (scan + duplicates + storage + largest files).

```bash
python -X utf8 main.py report <path>
```

**Examples:**

```bash
python -X utf8 main.py report .
python -X utf8 main.py report "C:\Users\YourName\Downloads" --workers 4
```

**Sample output:**

```
══════════════════ FILESENSE REPORT ══════════════════

┌─────────────────────── Summary ─────────────────────┐
│  Directory:          C:\Users\YourName\Downloads     │
│  Files:              48,291                          │
│  Directories:        3,218                           │
│  Total Storage:      182.4 GB                        │
│                                                      │
│  Duplicate Groups:   421                             │
│  Duplicate Files:    3,841                           │
│  Duplicate Storage:  27.8 GB                         │
│  Potential Savings:  24.1 GB                         │
└──────────────────────────────────────────────────────┘
```

---

### 6. `benchmark`

Compare naive full hashing vs the multi-stage pipeline. Measures time, disk I/O, and memory.

```bash
python -X utf8 main.py benchmark <path>
```

**Examples:**

```bash
# Default: test with workers 1,2,4
python -X utf8 main.py benchmark .

# Custom worker configs
python -X utf8 main.py benchmark ./datasets --workers 1,2,4,8
```

**Sample output:**

```
┌─────────────────────────────── Benchmark Results ─────────────────────────────────┐
│ Method              Workers  Time(s)  Disk Read  Hash Ops  Dup Groups  Throughput  │
│ Naive (full hash)     1      48.200   100.0 GB     50000       421     2.1 MB/s    │
│ Multi-stage           1      19.700    42.0 GB     21000       421     2.1 MB/s    │
│ Multi-stage           2      11.400    42.0 GB     21000       421     3.7 MB/s    │
│ Multi-stage           4       7.100    42.0 GB     21000       421     5.9 MB/s    │
└────────────────────────────────────────────────────────────────────────────────────┘

┌─── Improvement ────────────────────┐
│ Time saved:   41.1s  (85% faster)  │
│ I/O saved:    58.0 GB              │
└────────────────────────────────────┘
```

---

### 7. `clean` (dry-run)

Preview which files could be deleted to recover space. **Never deletes without explicit confirmation.**

```bash
# Safe preview only (default — no files are touched)
python -X utf8 main.py clean <path> --dry-run

# Actual deletion (requires typing 'y' at the prompt)
python -X utf8 main.py clean <path> --no-dry-run
```

**Examples:**

```bash
# Preview what could be cleaned
python -X utf8 main.py clean "C:\Users\YourName\Downloads"

# Perform actual deletion (will ask for confirmation)
python -X utf8 main.py clean "C:\Users\YourName\Downloads" --no-dry-run
```

**Sample output (dry-run):**

```
[!] Dry Run — This will NOT delete any files.
Potential recovery: 24.1 GB

Group #1
  KEEP    C:\Documents\report.pdf
  DELETE  C:\Backup\report.pdf
  DELETE  C:\Downloads\report_copy.pdf

Run with --no-dry-run to perform actual deletion.
```

> **Safety:** The default is always `--dry-run`. Actual deletion requires
> `--no-dry-run` AND typing `y` at the confirmation prompt.

---

### 8. `index status`

Show the current state of the persistent SQLite index
(`~/.filesense/filesense.db`).

```bash
python -X utf8 main.py index status
```

**Sample output:**

```
┌──────────────── Index Status ───────────────────┐
│  Database          C:\Users\hp\.filesense\...   │
│  Indexed files     88                           │
│  Hashed files      4                            │
│  Total indexed     218.0 KB                     │
└─────────────────────────────────────────────────┘
```

---

## Project Structure

```
FileSense/
├── filesense/
│   ├── metadata.py        # FileMetadata dataclass + extension classifier
│   ├── scanner.py         # FileScanner — recursive directory traversal
│   ├── hasher.py          # HashManager, PartialHashStrategy, FullHashStrategy
│   ├── index.py           # PersistentIndex — SQLite cache (~/.filesense/)
│   ├── duplicate.py       # DuplicateDetector — multi-stage pipeline
│   ├── storage.py         # StorageAnalyzer — usage breakdowns
│   ├── report.py          # ReportGenerator — Rich terminal output
│   ├── benchmark.py       # BenchmarkEngine — naive vs multi-stage
│   ├── logging_config.py  # Rotating log handler (logs/filesense.log)
│   └── cli.py             # Typer CLI — all 8 commands
├── tests/
│   ├── test_scanner.py    # 18 tests — scanner + metadata
│   ├── test_hasher.py     # 13 tests — hashing strategies
│   ├── test_duplicate.py  # 11 tests — duplicate detection pipeline
│   └── test_storage.py    # 13 tests — storage analysis
├── logs/
│   └── filesense.log      # Auto-rotating application log
├── main.py                # Entry point
├── requirements.txt
└── README.md
```

---

## Architecture

```
CLI (Typer + Rich)
    └── Commands
            ├── FileScanner      → discovers files, collects metadata
            ├── PersistentIndex  → SQLite cache (incremental scans)
            ├── HashManager      → PartialHasher + FullHasher (concurrent)
            ├── DuplicateDetector → multi-stage pipeline
            ├── StorageAnalyzer  → usage breakdown
            ├── ReportGenerator  → formatted Rich output
            └── BenchmarkEngine  → naive vs multi-stage comparison
```

---

## Pipeline

```
All files discovered
        │
        ▼
Group by SIZE          ← free (no disk reads)
        │
  >= 2 files?──No──► Skip (unique size, cannot be duplicate)
        │Yes
        ▼
Partial Hash           ← reads only 32 KB head + 32 KB tail
(concurrent)
        │
  >= 2 files?──No──► Skip
        │Yes
        ▼
Full SHA-256           ← streaming reads, never loads full file into RAM
(concurrent)
        │
  >= 2 files?──No──► Skip
        │Yes
        ▼
  DUPLICATE GROUP
```

**Why this is faster than naive hashing:**

| Stage        | Files hashed | Bytes read per file |
|-------------|-------------|---------------------|
| Naive        | All files   | Full file size       |
| Size filter  | Only same-size groups | 0 bytes |
| Partial hash | Size matches only | 64 KB max |
| Full hash    | Partial hash matches only | Full file size |

On a real dataset, the multi-stage approach typically reads **50-60% less data**
from disk compared to naive full hashing.
