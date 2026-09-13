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

> **Windows:** Always use `python -X utf8 main.py` to avoid encoding errors.

```bash
# Scan a directory
python -X utf8 main.py scan <path> [--workers N] [--follow-symlinks] [--verbose]

# Find duplicates
python -X utf8 main.py duplicates <path> [--workers N]

# Storage breakdown by category and extension
python -X utf8 main.py analyze <path>

# Top N largest files
python -X utf8 main.py largest <path> [--top N]

# Full combined report
python -X utf8 main.py report <path> [--workers N]

# Benchmark naive vs multi-stage hashing
python -X utf8 main.py benchmark <path> [--workers 1,2,4,8]

# Preview cleanup (safe — never deletes)
python -X utf8 main.py clean <path> --dry-run

# Actual cleanup (requires confirmation prompt)
python -X utf8 main.py clean <path> --no-dry-run

# SQLite index statistics
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

For a full technical breakdown see [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md).
