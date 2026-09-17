<div align="center">

# FileSense

**A fast, local CLI-based file analysis and deduplication engine.**

Scan any directory, instantly find duplicate files, visualize storage usage, and reclaim wasted disk space — all from your terminal.

[![Latest Release](https://img.shields.io/github/v/release/Jkotwal01/FileSense?style=flat-square&color=brightgreen)](https://github.com/Jkotwal01/FileSense/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/github/license/Jkotwal01/FileSense?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-55%20passing-brightgreen?style=flat-square)](#testing)

</div>

---

## Table of Contents

- [What is FileSense?](#what-is-filesense)
- [Installation](#installation)
  - [Option A — Python Users (Recommended)](#option-a--python-users-recommended)
  - [Option B — Direct Binary (No Python Required)](#option-b--direct-binary-no-python-required)
- [Quick Start](#quick-start)
- [CLI Commands](#cli-commands)
  - [scan](#1-scan--discover-and-index-files)
  - [duplicates](#2-duplicates--find-duplicate-files)
  - [analyze](#3-analyze--storage-breakdown)
  - [largest](#4-largest--top-space-consumers)
  - [report](#5-report--full-report)
  - [benchmark](#6-benchmark--performance-comparison)
  - [clean](#7-clean--remove-duplicates-safely)
  - [index status](#8-index-status--database-info)
- [Project Structure](#project-structure)
- [For Developers](#for-developers)
- [How It Works](#how-it-works)

---

## What is FileSense?

FileSense helps you understand and clean up your storage:

- **Finds duplicate files** using a multi-stage hashing pipeline (size → partial hash → full SHA-256), much faster than naive full-file hashing.
- **Breaks down storage usage** by file type, category, and directory.
- **Identifies your largest files** instantly.
- **Maintains a persistent index** (SQLite) so repeated scans are near-instant — unchanged files are never rehashed.
- **Never deletes anything automatically** — a dry-run preview is always shown first.

---

## Installation

### Option A — Python Users (Recommended)

Requires **Python 3.12 or higher**.

```bash
pip install filesense-cli
```

That's it. The `filesense` command is now available **from any directory** in your terminal.

Verify the installation:

```bash
filesense --help
```

---

### Option B — Direct Binary (No Python Required)

If you do not have Python installed, download the pre-built standalone executable for your operating system.

**Step 1 — Go to the Releases page:**

👉 [https://github.com/Jkotwal01/FileSense/releases/latest](https://github.com/Jkotwal01/FileSense/releases/latest)

**Step 2 — Download the correct file for your OS:**

| Operating System | File to Download |
|-----------------|-----------------|
| Windows | `filesense-windows-amd64.exe` |
| macOS | `filesense-macos-amd64` |
| Linux | `filesense-linux-amd64` |

**Step 3 — Run it:**

**Windows:**
```powershell
# Open PowerShell in your Downloads folder and run:
.\filesense-windows-amd64.exe --help

# Or scan a directory directly:
.\filesense-windows-amd64.exe scan "C:\Users\YourName\Downloads"
```

**macOS / Linux:**
```bash
# First, make the file executable (one-time setup):
chmod +x filesense-macos-amd64

# Then run it:
./filesense-macos-amd64 --help

# Or scan a directory:
./filesense-macos-amd64 scan ~/Downloads
```

> **Tip:** To use `filesense` from anywhere without typing the full file name, move the binary to a directory on your system PATH:
> - **Windows:** Move the `.exe` to `C:\Windows\System32\` or add its folder to your PATH environment variable.
> - **macOS/Linux:** Move it to `/usr/local/bin/filesense`

---

## Quick Start

The most common use case — scan a directory and find duplicates:

```bash
# 1. Scan your Downloads folder
filesense scan "C:\Users\Name\Downloads"

# 2. See which files are duplicates
filesense duplicates "C:\Users\Name\Downloads"

# 3. See a full storage breakdown
filesense analyze "C:\Users\Name\Downloads"

# 4. Preview what could be deleted (safe — no files are touched)
filesense clean "C:\Users\Name\Downloads" --dry-run
```

---

## CLI Commands

### Understanding the Syntax

```
filesense <command> <path> [options]
```

- `<path>` — **Required.** The directory to operate on. Use `.` for the current directory.
- `[options]` — **Optional** flags. Do **not** type the brackets.

---

### 1. `scan` — Discover and Index Files

Recursively scans a directory, collects file metadata, detects duplicates, and saves everything to a local SQLite cache for fast future scans.

```bash
filesense scan <path> [--workers N] [--follow-symlinks] [--verbose]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--workers N` / `-w N` | `4` | Number of parallel hashing threads. Use `8` for fast SSDs. |
| `--follow-symlinks` | off | Follow symbolic links during traversal. |
| `--verbose` / `-v` | off | Print detailed debug logs for each file processed. |

**Examples:**
```bash
# Basic scan of current directory
filesense scan .

# Scan Downloads with 8 threads (faster on SSDs)
filesense scan "C:\Users\Name\Downloads" --workers 8

# Verbose scan to see every file being processed
filesense scan ./projects --verbose
```

---

### 2. `duplicates` — Find Duplicate Files

Displays all duplicate file groups, showing which files are identical, their sizes, and how much space you can recover by removing them.

```bash
filesense duplicates <path> [--workers N]
```

**Example:**
```bash
filesense duplicates "C:\Users\Name\Documents"
```

**Sample output:**
```
Group #1  a3f9c2d1...  150.2 MB x 3 copies  -> 300.4 MB recoverable
  KEEP    C:\Users\Name\Documents\project\report.pdf
  DUP     C:\Users\Name\Desktop\report_copy.pdf
  DUP     C:\Users\Name\Downloads\report (1).pdf
```

---

### 3. `analyze` — Storage Breakdown

Shows a breakdown of storage usage grouped by **file category** (Images, Videos, Documents, etc.) and **file extension** (`.pdf`, `.mp4`, `.zip`, etc.).

```bash
filesense analyze <path>
```

**Example:**
```bash
filesense analyze "C:\Users\Name\Downloads"
```

---

### 4. `largest` — Top Space Consumers

Lists the N largest individual files in a directory, sorted by size.

```bash
filesense largest <path> [--top N]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--top N` / `-n N` | `10` | Number of files to show. |

**Examples:**
```bash
# Show top 10 largest files
filesense largest .

# Show top 25 largest files
filesense largest "C:\Users\Name" --top 25
```

---

### 5. `report` — Full Report

Runs a complete analysis in one command: scan, duplicate detection, storage breakdown, and largest files.

```bash
filesense report <path> [--workers N]
```

**Example:**
```bash
filesense report "C:\Users\Name\Documents" --workers 8
```

---

### 6. `benchmark` — Performance Comparison

Measures the speed difference between a naive approach (hashing every file fully) and FileSense's multi-stage pipeline. Useful for understanding the performance gains on your specific hardware.

```bash
filesense benchmark <path> [--workers 1,2,4,8]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--workers <list>` | `1,2,4` | Comma-separated list of thread configs to test. |

**Examples:**
```bash
filesense benchmark ./data
filesense benchmark ./data --workers 1,4,8,16
```

---

### 7. `clean` — Remove Duplicates Safely

Previews or performs cleanup of duplicate files. **Default is always dry-run — no files are ever touched unless you explicitly confirm.**

```bash
# SAFE — Preview only (default). Shows what WOULD be deleted.
filesense clean <path> --dry-run

# DANGEROUS — Actual deletion. Requires typing 'y' at a confirmation prompt.
filesense clean <path> --no-dry-run
```

> ⚠️ **Safety guarantee:** FileSense always keeps the file with the shortest path (the most likely original) and only deletes confirmed duplicates. Even with `--no-dry-run`, you must manually type `y` to confirm before any file is deleted.

---

### 8. `index status` — Database Info

Shows statistics about the local SQLite cache stored at `~/.filesense/filesense.db`.

```bash
filesense index status
```

**Sample output:**
```
  Database         C:\Users\Name\.filesense\filesense.db
  Indexed files    12,482
  Hashed files     9,341
  Total size       47.2 GB
```

---

## Project Structure

```
FileSense/
├── filesense/
│   ├── metadata.py        # FileMetadata dataclass + file type classifier
│   ├── scanner.py         # FileScanner — recursive directory traversal
│   ├── hasher.py          # HashManager, PartialHashStrategy, FullHashStrategy
│   ├── index.py           # PersistentIndex — SQLite cache (~/.filesense/)
│   ├── duplicate.py       # DuplicateDetector — 3-stage pipeline
│   ├── storage.py         # StorageAnalyzer — usage metrics
│   ├── report.py          # ReportGenerator — Rich terminal output
│   ├── benchmark.py       # BenchmarkEngine — performance measurement
│   ├── logging_config.py  # Rotating log handler (logs/filesense.log)
│   └── cli.py             # Typer CLI — all 8 commands
├── tests/                 # 55 unit tests
├── docs/
│   └── INTERVIEW_GUIDE.md # In-depth technical deep dive
├── main.py                # Entry point
├── pyproject.toml         # Package configuration
└── requirements.txt       # Dependencies
```

---

## For Developers

### Setup

```bash
git clone https://github.com/Jkotwal01/FileSense.git
cd FileSense
pip install -r requirements.txt
```

### Running from source (without installing)

```bash
# Windows
python -X utf8 main.py scan .

# macOS / Linux
python main.py scan .
```

### Running Tests

```bash
# Run all 55 tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_scanner.py -v
python -m pytest tests/test_hasher.py -v
python -m pytest tests/test_duplicate.py -v
python -m pytest tests/test_storage.py -v
```

---

## How It Works

FileSense uses a **3-stage filtering pipeline** that minimizes disk I/O:

```
All files
    │
    ▼
Stage 1: Group by file SIZE         ← Free (no disk reads)
    │     Unique-size files eliminated immediately
    │
    ▼
Stage 2: Partial Hash (64 KB)       ← Reads only 32 KB from head + 32 KB from tail
    │     Most non-duplicates eliminated cheaply
    │
    ▼
Stage 3: Full SHA-256               ← Only reads files that survived both filters
    │     Confirms exact duplicates
    │
    ▼
  Duplicate groups (sorted by recoverable space)
```

On a 50 GB dataset, a naive approach reads **50 GB** from disk. FileSense typically reads only **~5 GB** — a 90%+ reduction in disk I/O.

For a complete technical breakdown of every class, algorithm, and design decision, see [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md).
