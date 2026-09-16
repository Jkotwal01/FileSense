# FileSense

A local CLI-based **file analysis and deduplication engine** built in Python 3.x.

---

## Requirements

- Python **3.12+**
- Windows / Linux / macOS

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Running Unit Tests

```bash
# Run all 55 tests
python -X utf8 -m pytest tests/ -v

# Run a specific test file
python -X utf8 -m pytest tests/test_scanner.py -v
python -X utf8 -m pytest tests/test_hasher.py -v
python -X utf8 -m pytest tests/test_duplicate.py -v
python -X utf8 -m pytest tests/test_storage.py -v
```

---

## CLI Commands

> **Windows:** Always use `python -X utf8 main.py` to avoid encoding errors when displaying terminal UI graphics.

### Understanding the Command Syntax
- `<path>`: **Required** argument. The directory you want to scan (e.g., `.`, `./Downloads`, `"C:\My Files"`).
- `[...]`: **Optional** flags. You do not type the brackets. These modify how the command runs.

---

### 1. `scan` — Discover and index files
Scans a directory, finds duplicates, and saves everything to a local SQLite cache.

```bash
python -X utf8 main.py scan <path> [--workers N] [--follow-symlinks] [--verbose]
```
**Options:**
- `--workers N` (or `-w N`): Set the number of concurrent hashing threads (default is 4). Use 8 for fast SSDs, or 1 to disable concurrency.
- `--follow-symlinks`: By default, symbolic links are ignored. Add this flag to follow them.
- `--verbose` (or `-v`): Prints detailed DEBUG logs to the terminal (e.g., showing exactly which files are being hashed).

**Example:**
```bash
python -X utf8 main.py scan "C:\Users\Name\Downloads" --workers 8 --verbose
```

---

### 2. `duplicates` — View duplicate groups
Displays exactly which files are duplicates of each other and how much space they waste.

```bash
python -X utf8 main.py duplicates <path> [--workers N]
```

---

### 3. `analyze` — Storage breakdown
Shows how your storage is being used, grouped by file category (Images, Videos, Code) and extension (`.mp4`, `.pdf`).

```bash
python -X utf8 main.py analyze <path>
```

---

### 4. `largest` — Top space consumers
Lists the biggest individual files in the directory.

```bash
python -X utf8 main.py largest <path> [--top N]
```
**Options:**
- `--top N`: How many files to show (default is 10).

**Example:**
```bash
python -X utf8 main.py largest . --top 20
```

---

### 5. `report` — Full combined output
Runs scan, duplicate detection, storage analysis, and largest files all in one massive report.

```bash
python -X utf8 main.py report <path> [--workers N]
```

---

### 6. `benchmark` — Performance comparison
Compares the speed of a naive full-hash approach vs FileSense's multi-stage pipeline.

```bash
python -X utf8 main.py benchmark <path> [--workers 1,2,4,8]
```
**Options:**
- `--workers <comma-list>`: The thread configurations you want to test. Default is `1,2,4`.

**Example:**
```bash
python -X utf8 main.py benchmark ./data --workers 1,4,8,16
```

---

### 7. `clean` — Safely remove duplicates
Previews or performs actual deletion of duplicate files to recover space.

```bash
# Preview what WOULD be deleted (SAFE - Default behavior)
python -X utf8 main.py clean <path> --dry-run

# Perform ACTUAL deletion (Requires typing 'y' at a prompt)
python -X utf8 main.py clean <path> --no-dry-run
```

---

### 8. `index status` — Check database stats
Shows how many files are currently cached in your local `~/.filesense/filesense.db` database.

```bash
python -X utf8 main.py index status
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
│   ├── test_scanner.py
│   ├── test_hasher.py
│   ├── test_duplicate.py
│   └── test_storage.py
├── docs/
│   └── INTERVIEW_GUIDE.md # Deep-dive technical reference
├── logs/
│   └── filesense.log
├── main.py
├── requirements.txt
└── README.md
```

---

## Pipeline

```
Files → Size Grouping → Partial Hash → Full SHA-256 → Duplicate Groups
```
Please do not try on C drives
For a full technical breakdown, see [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md).
