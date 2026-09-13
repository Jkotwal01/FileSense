# FileSense — Complete Technical Reference

> **Interview-ready deep dive into every class, design decision, and optimization.**

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Problem Statement & Core Insight](#2-problem-statement--core-insight)
3. [Architecture Overview](#3-architecture-overview)
4. [Design Patterns](#4-design-patterns)
5. [Class-by-Class Breakdown](#5-class-by-class-breakdown)
   - [FileMetadata](#51-filemetadata--metadatapy)
   - [FileScanner](#52-filescanner--scannerpy)
   - [HashStrategy / HashManager](#53-hashstrategy--hashmanager--hasherpy)
   - [PersistentIndex](#54-persistentindex--indexpy)
   - [DuplicateDetector](#55-duplicatedetector--duplicatepy)
   - [StorageAnalyzer](#56-storageanalyzer--storagepy)
   - [ReportGenerator](#57-reportgenerator--reportpy)
   - [BenchmarkEngine](#58-benchmarkengine--benchmarkpy)
   - [CLI](#59-cli--clipy)
6. [The Multi-Stage Pipeline — Core Algorithm](#6-the-multi-stage-pipeline--core-algorithm)
7. [Incremental Scanning](#7-incremental-scanning)
8. [Concurrency Design](#8-concurrency-design)
9. [Database Schema](#9-database-schema)
10. [Error Handling Strategy](#10-error-handling-strategy)
11. [Testing Strategy](#11-testing-strategy)
12. [Installation & Setup](#12-installation--setup)
13. [CLI Usage Guide](#13-cli-usage-guide)
14. [Interview Q&A](#14-interview-qa)

---

## 1. Project Overview

**FileSense** is a local CLI-based file analysis and deduplication engine.

| Property | Value |
|----------|-------|
| Language | Python 3.12+ |
| Database | SQLite (via `sqlite3` stdlib) |
| CLI | Typer + Rich |
| Hashing | `hashlib` SHA-256 |
| Concurrency | `concurrent.futures.ThreadPoolExecutor` |
| Testing | `pytest` (55 tests) |

### What it does

```
1. Scans a directory recursively
2. Collects file metadata (size, mtime, inode, extension)
3. Groups files by size   → eliminates impossible duplicates immediately
4. Hashes 64 KB per file → eliminates most remaining non-duplicates cheaply
5. Full SHA-256 hashes   → confirms exact duplicates
6. Reports storage waste, suggests cleanup, benchmarks performance
```

---

## 2. Problem Statement & Core Insight

### The Naive Approach (Wrong)

```python
# BAD: reads every single file completely
for file in all_files:
    hash = sha256(file.read())   # 100 GB dataset = 100 GB disk read
```

On a 100,000-file / 500 GB dataset this causes massive, unnecessary disk I/O.

### The Core Insight

> **Two files with different sizes can NEVER be exact duplicates.**
> Therefore, there is no reason to hash them against each other.

```
A.pdf  →  100 MB  ─┐
B.pdf  →  100 MB  ─┤  same size → must hash these
C.pdf  →   50 MB  ─┘  different size → skip immediately (free)
```

This single insight — filter by size before hashing — is the foundation of FileSense's
multi-stage pipeline and the reason it significantly outperforms naive hashing.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────┐
│           CLI  (Typer + Rich)        │
└──────────────────┬──────────────────┘
                   │ orchestrates
          ┌────────▼────────┐
          │  FileScanner    │  ← traverses directories
          └────────┬────────┘
                   │ produces list[FileMetadata]
          ┌────────▼────────┐
          │ PersistentIndex │  ← SQLite cache (incremental)
          └────────┬────────┘
                   │ enriched list[FileMetadata]
          ┌────────▼────────┐
          │DuplicateDetector│  ← multi-stage pipeline
          │                 │     uses HashManager internally
          └────────┬────────┘
                   │ list[DuplicateGroup]
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
StorageAnalyzer  Report     BenchmarkEngine
```

**Data flows in one direction** — no circular dependencies between modules.

---

## 4. Design Patterns

### Strategy Pattern — Hashing

The `HashStrategy` ABC defines the interface. Two concrete implementations exist:

```python
class HashStrategy(ABC):
    @abstractmethod
    def compute(self, path: str) -> str:
        """Return hex-digest hash for the given file path."""

class PartialHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        # reads first 32 KB + last 32 KB = 64 KB max
        ...

class FullHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        # streams entire file in 64 KB chunks
        ...
```

**Why Strategy Pattern here?**
- Adding a new hash algorithm (e.g. BLAKE3, MD5) requires only a new class
- The `HashManager` and `DuplicateDetector` never change
- Satisfies the Open/Closed Principle (open for extension, closed for modification)

### Single Responsibility Principle

Every class does exactly one thing:

| Class | Responsibility |
|-------|---------------|
| `FileMetadata` | Hold data for one file — no I/O |
| `FileScanner` | Traverse filesystem — no hashing |
| `HashManager` | Compute hashes — no file traversal |
| `PersistentIndex` | Read/write SQLite — no hashing |
| `DuplicateDetector` | Run the pipeline — no formatting |
| `StorageAnalyzer` | Compute metrics — no output |
| `ReportGenerator` | Format output — no computation |

---

## 5. Class-by-Class Breakdown

---

### 5.1 `FileMetadata` — `metadata.py`

**Purpose:** Pure data container for a single file. No I/O, no logic beyond
classification and comparison helpers.

```python
@dataclass
class FileMetadata:
    path: str           # absolute path — primary key
    filename: str       # display name
    extension: str      # lowercase (.pdf, .mp4 ...)
    size: int           # bytes — used for size grouping
    mtime: float        # last modified timestamp (epoch) — change detection
    ctime: float        # creation/change time
    inode: str          # OS file identity (inode on Linux, hash of path on Windows)
    permissions: str    # octal string (0o644)
    category: str       # auto-classified (Images/Videos/Code/...)
    partial_hash: str   # set after stage 2
    full_hash: str      # set after stage 3 — the duplicate key
    last_scanned: datetime
    status: str         # 'active' | 'deleted'
```

**Key methods:**

```python
def is_changed(self, other: FileMetadata) -> bool:
    """
    Returns True if size or mtime differs from the indexed record.
    Used by PersistentIndex to decide whether to rehash.

    Tolerance of 0.001s on mtime handles filesystem precision differences
    (FAT32 rounds mtime to 2-second intervals).
    """
    return self.size != other.size or abs(self.mtime - other.mtime) > 0.001

def human_size(self) -> str:
    """Converts raw bytes to human-readable string: 5,242,880 → '5.0 MB'"""
```

**`__post_init__` auto-classification:**

```python
def __post_init__(self) -> None:
    if not self.category or self.category == "Other":
        self.category = classify_extension(self.extension)
```

The `EXTENSION_CATEGORIES` dict maps 60+ extensions to 8 categories:

```python
EXTENSION_CATEGORIES = {
    ".jpg": "Images",  ".mp4": "Videos",
    ".pdf": "Documents", ".py": "Code",
    ".zip": "Archives", ".mp3": "Audio",
    ".exe": "Executables",
    # ... 50+ more
}
```

**Interview talking point:** Using a `@dataclass` keeps the class declaration
minimal while still providing `__init__`, `__repr__`, and `__eq__` for free.
The `is_changed()` method encapsulates the incremental scan comparison logic
directly on the data object — keeping it close to the data it describes.

---

### 5.2 `FileScanner` — `scanner.py`

**Purpose:** Recursively traverse a directory tree, `stat()` every file, and
return a `ScanResult` containing all `FileMetadata` objects.

```python
class FileScanner:
    def __init__(self, follow_symlinks: bool = False) -> None:
        self.follow_symlinks = follow_symlinks

    def scan(self, root: str | Path) -> ScanResult:
        """Entry point — validates root, delegates to _walk()."""

    def _walk(self, root: Path, result: ScanResult) -> Iterator[Path]:
        """
        Recursive generator that yields file paths.
        Accumulates directory count and errors in ScanResult.
        Catches PermissionError and OSError per-entry — never terminates early.
        """

    def _collect_metadata(self, path: Path) -> FileMetadata:
        """Calls path.stat() and builds FileMetadata from the OS response."""
```

**`ScanResult` — aggregate container:**

```python
class ScanResult:
    files: list[FileMetadata]
    directory_count: int
    error_count: int
    errors: list[str]       # human-readable error messages

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)
```

**Symlink handling:**

```python
# Explicit pre-check avoids pathlib stub compatibility issues
if entry.is_symlink():
    if not self.follow_symlinks:
        continue          # skip (default behaviour)
    resolved = entry.resolve()
    if resolved.is_dir():
        yield from self._walk(entry, result)  # follow into dir
    elif resolved.is_file():
        yield entry                            # treat as file
elif entry.is_dir():
    result.directory_count += 1
    yield from self._walk(entry, result)
elif entry.is_file():
    yield entry
```

**Error handling philosophy:**

```python
try:
    entries = list(root.iterdir())
except PermissionError:
    result.add_error(f"Permission denied (directory): {root}")
    return   # skip this directory, continue with others
```

Every error is logged as a WARNING and added to `ScanResult.errors`. The scan
**never raises** unless the root path itself is invalid.

**Interview talking point:** The generator pattern (`yield from self._walk()`)
keeps memory usage O(depth) for the call stack rather than O(n) for the file
list — though in practice we collect to a list for the pipeline. The key design
choice is that `_walk` and `_collect_metadata` are separate methods: `_walk`
only knows about *paths*, while `_collect_metadata` knows how to *stat* them.
This makes each independently testable.

---

### 5.3 `HashStrategy` / `HashManager` — `hasher.py`

**Purpose:** Compute file hashes at two levels of depth using the Strategy pattern.

#### `PartialHashStrategy`

```python
PARTIAL_READ_BYTES = 32 * 1024   # 32 KB

class PartialHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        hasher = hashlib.sha256()
        file_size = Path(path).stat().st_size

        with open(path, "rb") as fh:
            head = fh.read(PARTIAL_READ_BYTES)     # first 32 KB
            hasher.update(head)

            if file_size > PARTIAL_READ_BYTES * 2:
                fh.seek(-PARTIAL_READ_BYTES, 2)    # seek from end
                tail = fh.read(PARTIAL_READ_BYTES) # last 32 KB
                hasher.update(tail)

        return hasher.hexdigest()
```

**Why head + tail?**
- The head catches differences in file headers (format metadata)
- The tail catches differences in file endings (compression trailers, padding)
- Reading only 64 KB maximum regardless of file size is the key optimization

**Edge case:** files smaller than 64 KB — the tail read is skipped to avoid
double-reading. This means very small files get hashed from their entire content
via the head read alone.

#### `FullHashStrategy`

```python
STREAM_CHUNK_BYTES = 64 * 1024   # 64 KB per read

class FullHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            while chunk := fh.read(STREAM_CHUNK_BYTES):   # walrus operator
                hasher.update(chunk)
        return hasher.hexdigest()
```

**Why streaming?** A 10 GB file must never be loaded into RAM. Streaming reads
64 KB at a time — memory usage stays constant at ~64 KB regardless of file size.
This directly satisfies AC-03 and NFR-02.

**The walrus operator (`:=`)** assigns and checks in one expression — equivalent
to:
```python
chunk = fh.read(STREAM_CHUNK_BYTES)
while chunk:
    hasher.update(chunk)
    chunk = fh.read(STREAM_CHUNK_BYTES)
```

#### `HashManager`

```python
class HashManager:
    def __init__(self) -> None:
        self._partial = PartialHashStrategy()
        self._full = FullHashStrategy()

    def compute_partial(self, meta: FileMetadata) -> FileMetadata:
        """
        Computes and stores partial_hash on the metadata object.
        Skips computation if partial_hash is already set (cache hit).
        Catches all OS errors gracefully.
        """

    def compute_full(self, meta: FileMetadata) -> FileMetadata:
        """Same pattern for full_hash."""
```

**Skip-if-cached logic:**

```python
def compute_partial(self, meta: FileMetadata) -> FileMetadata:
    if meta.partial_hash:      # already computed (from index or prior run)
        return meta
    try:
        meta.partial_hash = self._partial.compute(meta.path)
    except PermissionError:
        logger.warning("Permission denied: %s", meta.path)
    except FileNotFoundError:
        logger.warning("File disappeared: %s", meta.path)
    return meta
```

This means `HashManager` is safe to call on any metadata object — it
self-optimises by checking the cache first.

**Interview talking point:** The Strategy pattern here gives a clear answer to
"how would you add BLAKE3 hashing?" — implement `BLAKE3HashStrategy(HashStrategy)`,
pass it to `HashManager`, done. No other class changes.

---

### 5.4 `PersistentIndex` — `index.py`

**Purpose:** SQLite-backed cache that stores file metadata and computed hashes,
enabling incremental scanning (avoid rehashing unchanged files).

**Database location:** `~/.filesense/filesense.db`

```python
class PersistentIndex:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()
```

**Why WAL (Write-Ahead Logging) mode?**
SQLite's default journal mode locks the database for the duration of a write.
WAL allows concurrent reads while a write is in progress — critical when
concurrent hashing workers want to update the index simultaneously.

**Core methods:**

```python
def upsert(self, meta: FileMetadata) -> None:
    """
    INSERT OR UPDATE using SQLite's ON CONFLICT DO UPDATE syntax.
    A single statement handles both new and existing records.
    """
    self._conn.execute("""
        INSERT INTO files (path, size, mtime, ...) VALUES (...)
        ON CONFLICT(path) DO UPDATE SET
            size = excluded.size,
            mtime = excluded.mtime,
            ...
    """)

def is_changed(self, meta: FileMetadata) -> bool:
    """
    Compares current file's size+mtime against the indexed record.
    Returns True if the file is new OR has been modified.
    """
    indexed = self.get(meta.path)
    if indexed is None:
        return True          # never indexed before
    return meta.is_changed(indexed)

def load_hashes(self, meta: FileMetadata) -> FileMetadata:
    """
    The KEY method for incremental scanning.
    If the file is unchanged, copies partial_hash and full_hash
    from the index into the metadata object — transparently.
    The caller (DuplicateDetector) never knows whether hashes came
    from disk or from the index.
    """
    if not self.is_changed(meta):
        indexed = self.get(meta.path)
        if indexed:
            meta.partial_hash = indexed.partial_hash
            meta.full_hash = indexed.full_hash
    return meta
```

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT UNIQUE NOT NULL,   -- primary key for lookups
    size         INTEGER,
    mtime        REAL,                   -- float epoch timestamp
    inode        TEXT,
    extension    TEXT,
    partial_hash TEXT,
    full_hash    TEXT,
    last_scanned DATETIME,
    status       TEXT DEFAULT 'active'  -- 'active' | 'deleted'
);

CREATE INDEX idx_files_path      ON files(path);
CREATE INDEX idx_files_full_hash ON files(full_hash);
```

**Why index `full_hash`?** Future queries like "find all files with hash X"
are O(log n) rather than O(n).

**Interview talking point:** The `load_hashes()` method is the cleanest part of
the incremental scan design. The `DuplicateDetector` calls it on each file before
running the pipeline — hashes from the index are indistinguishable from freshly
computed ones, so the detector code is completely unaware of caching. This is
the **Transparent Proxy** pattern.

---

### 5.5 `DuplicateDetector` — `duplicate.py`

**Purpose:** Run the complete multi-stage pipeline and return `DuplicateGroup`
objects. This is the core engine of FileSense.

```python
class DuplicateDetector:
    def __init__(self, workers: int = 4) -> None:
        self.workers = max(1, workers)
        self._hasher = HashManager()

    def detect(self, files: list[FileMetadata]) -> list[DuplicateGroup]:
        """Runs all three pipeline stages and returns sorted duplicate groups."""
```

**Pipeline implementation:**

```python
def detect(self, files):
    # Stage 1 — size grouping (zero disk I/O)
    size_candidates = self._group_by_size(files)

    # Stage 2 — partial hashing (concurrent, 64 KB per file max)
    flat = [f for group in size_candidates.values() for f in group]
    self._hash_concurrent(flat, stage="partial")
    partial_candidates = self._group_by_partial_hash(flat)

    # Stage 3 — full hashing (concurrent, streaming)
    flat_full = [f for group in partial_candidates.values() for f in group]
    self._hash_concurrent(flat_full, stage="full")

    return self._build_duplicate_groups(flat_full)
```

**Grouping helpers:**

```python
def _group_by_size(self, files) -> dict[int, list[FileMetadata]]:
    size_map = defaultdict(list)
    for meta in files:
        size_map[meta.size].append(meta)
    # Only return groups with >= 2 files — singletons cannot be duplicates
    return {size: group for size, group in size_map.items() if len(group) >= 2}
```

**Concurrent hashing:**

```python
def _hash_concurrent(self, files, stage):
    # Skip files that already have their hash (index cache hits)
    if stage == "partial":
        needs_hash = [f for f in files if not f.partial_hash]
        hash_fn = self._hasher.compute_partial
    else:
        needs_hash = [f for f in files if not f.full_hash]
        hash_fn = self._hasher.compute_full

    with ThreadPoolExecutor(max_workers=self.workers) as pool:
        futures = {pool.submit(hash_fn, meta): meta for meta in needs_hash}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                logger.error("Hash failed for %s: %s", futures[future].path, exc)
```

**`DuplicateGroup`:**

```python
@dataclass
class DuplicateGroup:
    full_hash: str
    files: list[FileMetadata]

    @property
    def wasted_size(self) -> int:
        """Bytes freed by keeping one copy: size × (count - 1)"""
        return self.file_size * (self.file_count - 1)

    @property
    def original_candidate(self) -> FileMetadata:
        """Heuristic: shortest path is most likely the 'original'."""
        return min(self.files, key=lambda f: len(f.path))
```

Groups are sorted by `wasted_size` descending — the most impactful duplicates
appear first in every report.

**Interview talking point:** The three-stage filter is analogous to database
query optimisation — apply the cheapest filter first (size comparison is free),
then progressively more expensive ones (partial hash = 64 KB read, full hash =
full file read). Only files that survive all filters are confirmed duplicates.

---

### 5.6 `StorageAnalyzer` — `storage.py`

**Purpose:** Pure analytics over a list of `FileMetadata` objects. No I/O,
no output — returns structured dicts and lists.

```python
class StorageAnalyzer:

    def total_usage(self, files: list[FileMetadata]) -> dict:
        """Returns file_count, total_bytes, total_human."""

    def by_extension(self, files: list[FileMetadata]) -> list[dict]:
        """
        Groups files by extension, computes per-extension count and bytes.
        Returns sorted by total bytes descending.
        Uses defaultdict(lambda: {"count": 0, "bytes": 0}) for clean aggregation.
        """

    def by_category(self, files: list[FileMetadata]) -> list[dict]:
        """Same pattern grouped by category (Images/Videos/Code/...)"""

    def by_directory(self, files: list[FileMetadata]) -> list[dict]:
        """Groups by immediate parent directory. Sorted by size descending."""

    def largest_files(self, files: list[FileMetadata], top: int = 10) -> list[FileMetadata]:
        """Returns top N files sorted by size descending."""
        return sorted(files, key=lambda f: f.size, reverse=True)[:top]

    def duplicate_summary(self, groups: list[DuplicateGroup]) -> dict:
        """
        Aggregates across all duplicate groups:
          - total duplicate file count
          - total bytes in duplicate files
          - total wasted bytes (savings if deduplicated)
        """
```

**Key pattern — `defaultdict` aggregation:**

```python
def by_extension(self, files):
    ext_map = defaultdict(lambda: {"count": 0, "bytes": 0})
    for meta in files:
        ext = meta.extension or "(no ext)"
        ext_map[ext]["count"] += 1
        ext_map[ext]["bytes"] += meta.size
    # Convert to list and sort
    result = [{"extension": ext, **data} for ext, data in ext_map.items()]
    result.sort(key=lambda x: x["bytes"], reverse=True)
    return result
```

**Interview talking point:** `StorageAnalyzer` is completely pure — given the
same input it always produces the same output. This makes it trivially testable
(13 unit tests, no mocking needed) and reusable across different contexts (CLI,
benchmark reports, future web API).

---

### 5.7 `ReportGenerator` — `report.py`

**Purpose:** Format and print analysis results using Rich. The only class that
produces terminal output.

```python
class ReportGenerator:
    def __init__(self) -> None:
        self._analyzer = StorageAnalyzer()   # used internally for metrics

    def full_report(self, scan_result, duplicate_groups, root) -> None:
        """Combined scan + duplicate + storage + largest files report."""

    def scan_summary(self, scan_result, elapsed) -> None:
        """Brief panel shown after every scan command."""

    def duplicate_report(self, groups) -> None:
        """Lists every duplicate group with KEEP/DUP labels."""

    def storage_report(self, files) -> None:
        """Category and extension breakdown tables."""

    def extension_report(self, files, top=15) -> None:
        """Top N extensions by storage — Rich Table."""

    def largest_files_report(self, files, top=10) -> None:
        """Top N largest files — Rich Table."""

    def cleanup_preview(self, groups) -> None:
        """Dry-run view — shows KEEP and DELETE candidates, never deletes."""

    def index_status_report(self, stats) -> None:
        """Panel showing SQLite index statistics."""
```

**Rich usage pattern:**

```python
# Panel for summaries
console.print(Panel(
    f"[bold]Total Storage:[/bold] [cyan]{usage['total_human']}[/cyan]",
    title="Summary",
    border_style="cyan"
))

# Table for structured data
table = Table(title="Top Extensions", box=box.SIMPLE_HEAD)
table.add_column("Extension", style="bold")
table.add_column("Files", justify="right")
table.add_column("Storage", justify="right", style="cyan")
for row in data:
    table.add_row(row["extension"], str(row["count"]), row["human"])
console.print(table)
```

**Interview talking point:** Separating `StorageAnalyzer` (compute) from
`ReportGenerator` (display) follows the MVC pattern — the Model (`FileMetadata`),
the analysis logic (`StorageAnalyzer`), and the View (`ReportGenerator`) are
fully decoupled. You could add a JSON output formatter or a future web API
response without touching any computation code.

---

### 5.8 `BenchmarkEngine` — `benchmark.py`

**Purpose:** Empirically measure and compare naive vs multi-stage hashing.

```python
@dataclass
class BenchmarkResult:
    method: str
    workers: int
    elapsed_seconds: float
    files_processed: int
    bytes_read: int          # estimated actual disk reads
    hash_operations: int
    duplicate_groups: int
    peak_memory_bytes: int   # from tracemalloc

    def throughput_mbs(self) -> float:
        return (self.bytes_read / 1024 / 1024) / self.elapsed_seconds


class BenchmarkEngine:
    def __init__(self, workers_list: list[int] = [1, 2, 4]) -> None:
        self.workers_list = workers_list

    def run(self, path: str) -> list[BenchmarkResult]:
        """Scans the directory, then runs naive + all multi-stage configs."""

    def _run_naive(self, files) -> BenchmarkResult:
        """Hash every file completely with FullHashStrategy. No filtering."""

    def _run_multistage(self, files, workers) -> BenchmarkResult:
        """Full 3-stage pipeline with given worker count."""
```

**Memory measurement with `tracemalloc`:**

```python
tracemalloc.start()
t_start = time.perf_counter()

# ... hashing work ...

elapsed = time.perf_counter() - t_start
_, peak = tracemalloc.get_traced_memory()   # peak memory since start()
tracemalloc.stop()
```

**Fresh copies for fair comparison:**

```python
import copy
fresh = [copy.copy(f) for f in files]
for f in fresh:
    f.partial_hash = None    # clear any cached hashes
    f.full_hash = None       # so each run starts from scratch
```

**Interview talking point:** The benchmark is the scientific validation of the
pipeline's design. It answers "how much faster is multi-stage?" with actual
numbers from the user's own hardware and dataset. `tracemalloc` is a stdlib
module — no external profiler needed — that captures peak heap allocation
within a traced block.

---

### 5.9 CLI — `cli.py`

**Purpose:** Expose all functionality through Typer commands. Orchestrates all
other classes in the correct order.

```python
app = typer.Typer(name="filesense", help="...")

@app.command()
def scan(
    path: str = typer.Argument(...),
    workers: int = typer.Option(4, "--workers", "-w"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None: ...
```

**Orchestration pattern inside each command:**

```python
# Every command follows this same flow:
scanner = FileScanner()
scan_result = scanner.scan(root)       # 1. scan

index = PersistentIndex()
for meta in scan_result.files:
    index.load_hashes(meta)            # 2. load cached hashes

detector = DuplicateDetector(workers)
groups = detector.detect(files)        # 3. detect duplicates

for meta in scan_result.files:
    index.upsert(meta)                 # 4. persist updated hashes
index.close()

ReportGenerator().some_report(...)    # 5. display results
```

**Safety enforcement for `clean`:**

```python
@app.command()
def clean(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    reporter.cleanup_preview(groups)    # always shown

    if not dry_run:                     # requires explicit --no-dry-run
        confirmed = Confirm.ask("Continue?", default=False)  # default = No
        if confirmed:
            for group in groups:
                for dup in group.duplicates:
                    Path(dup.path).unlink()
```

Three layers of safety: (1) default flag is `--dry-run`, (2) requires explicit
`--no-dry-run` to reach deletion code, (3) requires typing `y` at a prompt
whose default is `N`.

---

## 6. The Multi-Stage Pipeline — Core Algorithm

```
All files
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Group by SIZE                    (0 disk I/O)      │
│                                                              │
│  size_map = defaultdict(list)                                │
│  for f in files: size_map[f.size].append(f)                  │
│  candidates = {size: group for ... if len(group) >= 2}       │
│                                                              │
│  Example: 10,000 files → 3,000 size-match candidates         │
└─────────────────────────────────────────────────────────────┘
    │  ~3,000 files remain (70% eliminated for free)
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: Partial Hash (32 KB head + 32 KB tail)             │
│                                                              │
│  Concurrent with ThreadPoolExecutor                          │
│  Max 64 KB read per file                                     │
│                                                              │
│  Example: 3,000 files → 200 partial-hash-match candidates    │
└─────────────────────────────────────────────────────────────┘
    │  ~200 files remain (98% eliminated with 64 KB reads)
    ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: Full SHA-256 (streaming, concurrent)               │
│                                                              │
│  Reads entire file in 64 KB chunks                           │
│  Only for files that survived both previous stages           │
│                                                              │
│  Example: 200 files → 80 confirmed duplicate groups          │
└─────────────────────────────────────────────────────────────┘
    │  Confirmed exact duplicates
    ▼
  DuplicateGroup list — sorted by wasted_size descending
```

**I/O comparison (10,000 file / 50 GB dataset example):**

| Approach | Files fully read | Bytes read |
|----------|-----------------|------------|
| Naive | 10,000 | 50 GB |
| FileSense Stage 1 | 3,000 | 0 (free) |
| FileSense Stage 2 | 3,000 | 187.5 MB (3000 × 64 KB) |
| FileSense Stage 3 | 200 | ~5 GB (only candidates) |
| **FileSense total** | **200** | **~5.2 GB** |
| **Savings** | | **~44.8 GB less disk read** |

---

## 7. Incremental Scanning

On repeated scans of the same directory, unchanged files must not be rehashed.

```
Second scan of the same directory:

For each discovered file:
  ┌─────────────────────────────────────────────┐
  │ index.load_hashes(meta)                      │
  │                                              │
  │   1. Look up path in SQLite                  │
  │   2. Compare size + mtime                    │
  │      ├── Same → copy hashes from index       │
  │      │         meta.partial_hash = cached    │
  │      │         meta.full_hash = cached       │
  │      └── Changed → hashes remain None        │
  │             (will be recomputed by pipeline) │
  └─────────────────────────────────────────────┘
```

**Key implementation detail:**

```python
# In DuplicateDetector._hash_concurrent:
needs_hash = [f for f in files if not f.partial_hash]   # skip if cached
```

Files with `partial_hash` already set (from the index) are silently skipped
by the hasher. The pipeline treats them identically to freshly hashed files.

**Result:** On a second scan of an unchanged 100 GB directory:
- Disk reads: ~0 bytes (all hashes from SQLite)
- Time: seconds instead of minutes

---

## 8. Concurrency Design

**Why `ThreadPoolExecutor` and not `ProcessPoolExecutor`?**

File hashing is **I/O-bound** (reading from disk), not **CPU-bound** (computing).
- The GIL (Global Interpreter Lock) is released during `fh.read()` (a system call)
- Multiple threads can therefore genuinely run simultaneously during disk reads
- `ProcessPoolExecutor` adds process spawn overhead for no benefit on I/O tasks

```python
with ThreadPoolExecutor(max_workers=self.workers) as pool:
    # Submit all files concurrently
    futures = {pool.submit(hash_fn, meta): meta for meta in needs_hash}

    # Collect results as they complete (not in submission order)
    for future in as_completed(futures):
        try:
            future.result()
        except Exception as exc:
            logger.error("Hash failed for %s: %s", futures[future].path, exc)
```

**`as_completed()` vs `pool.map()`:**
- `as_completed()` yields results as each finishes — no blocking on slow files
- `pool.map()` blocks until ALL results are ready before yielding any

**Configurable workers:**

```bash
python -X utf8 main.py scan ./data --workers 1   # baseline
python -X utf8 main.py scan ./data --workers 4   # default
python -X utf8 main.py scan ./data --workers 8   # high I/O hardware
```

Benchmark to find the optimal worker count for your hardware — more workers
is not always faster (disk I/O saturation, thread scheduling overhead).

---

## 9. Database Schema

```sql
-- Primary table: one row per discovered file
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT UNIQUE NOT NULL,  -- absolute path, PK for upserts
    size         INTEGER,               -- bytes
    mtime        REAL,                  -- last modified (epoch float)
    inode        TEXT,                  -- OS file identity
    extension    TEXT,                  -- lowercase (.pdf, .mp4...)
    partial_hash TEXT,                  -- set after stage 2
    full_hash    TEXT,                  -- set after stage 3, NULL until then
    last_scanned DATETIME,              -- ISO format UTC timestamp
    status       TEXT DEFAULT 'active'  -- 'active' | 'deleted'
);

-- Indexes for fast lookups
CREATE INDEX idx_files_path      ON files(path);       -- O(log n) path lookup
CREATE INDEX idx_files_full_hash ON files(full_hash);  -- future: find by hash
```

**Upsert pattern (single SQL statement):**

```sql
INSERT INTO files (path, size, mtime, ...)
VALUES (...)
ON CONFLICT(path) DO UPDATE SET
    size         = excluded.size,
    mtime        = excluded.mtime,
    partial_hash = excluded.partial_hash,
    full_hash    = excluded.full_hash,
    last_scanned = excluded.last_scanned,
    status       = 'active';
```

This is atomic and avoids the need for a separate SELECT + INSERT/UPDATE.

---

## 10. Error Handling Strategy

| Error Type | Where | Behaviour |
|-----------|-------|-----------|
| `PermissionError` on directory | `FileScanner._walk` | Log WARNING, skip directory, continue |
| `PermissionError` on file stat | `FileScanner.scan` | Log WARNING, skip file, continue |
| `FileNotFoundError` during hash | `HashManager` | Log WARNING, partial/full_hash stays None |
| `OSError` during hash | `HashManager` | Log ERROR, hash stays None |
| Root path not found | `FileScanner.scan` | Raise `FileNotFoundError` (unrecoverable) |
| Root is a file not dir | `FileScanner.scan` | Raise `NotADirectoryError` (unrecoverable) |
| SQLite error | `PersistentIndex` | Propagates (unrecoverable — program state corrupt) |

**Logging levels:**

```
DEBUG   → per-file hash OK messages (verbose mode only)
INFO    → scan started/completed, pipeline stage counts
WARNING → permission denied, file disappeared
ERROR   → unexpected OS errors during hashing
```

Logs are written to `logs/filesense.log` with rotating file handler
(max 5 MB × 3 backup files).

---

## 11. Testing Strategy

### Test coverage (55 tests across 4 files)

| File | Tests | What is covered |
|------|-------|-----------------|
| `test_scanner.py` | 18 | Extension classification, FileMetadata dataclass, recursive scan, error cases, metadata accuracy |
| `test_hasher.py` | 13 | Same content = same hash, different content = different hash, empty files, large files, skip-if-cached |
| `test_duplicate.py` | 11 | Exact duplicates found, different sizes not compared, filename irrelevant, cached hashes reused |
| `test_storage.py` | 13 | Total usage, extension/category breakdown sorting, largest files ordering, duplicate savings |

### Run all tests

```bash
python -X utf8 -m pytest tests/ -v
```

### Acceptance criteria verified by tests

```
AC-01 ✅  test_finds_exact_duplicates          — identical files detected
AC-02 ✅  test_different_sizes_not_duplicates  — size filter works
AC-03 ✅  test_large_file_streams              — 2 MB hashed without OOM
AC-04 ✅  test_cached_hashes_reused            — index cache used on rescan
AC-05 ✅  test_duplicate_summary               — savings calculated correctly
AC-06 ✅  FileScanner error handling           — scan continues on permission errors
AC-07 ✅  BenchmarkEngine                      — produces measurable results
AC-08 ✅  clean --dry-run                      — no deletion without confirmation
```

### Business rules verified by tests

```
BR-01 ✅  test_same_name_different_content_not_duplicate
          — filename is NOT the duplicate criterion

BR-02 ✅  test_different_name_same_content_is_duplicate
          — content identity (SHA-256) is the only criterion

BR-06 ✅  clean command defaults to --dry-run
          — deletion requires explicit --no-dry-run + 'y' confirmation
```

---

## 12. Installation & Setup

```bash
# Prerequisites: Python 3.12+
python --version    # should show 3.12 or higher

# Install dependencies
pip install -r requirements.txt
```

**Dependencies:**

```
typer[all]   — Typer CLI framework with Rich integration
rich         — Coloured terminal output (tables, panels, progress)
pytest       — Unit testing framework
psutil       — Memory/process info for benchmarks
```

**Verify installation:**

```bash
python -X utf8 -m pytest tests/ -v         # all 55 tests should pass
python -X utf8 main.py --help              # CLI help
python -X utf8 main.py index status        # check SQLite index
```

> **Windows note:** Always prefix with `python -X utf8` to enable UTF-8
> terminal mode and avoid `UnicodeEncodeError` from Rich's output.

---

## 13. CLI Usage Guide

### Commands

```bash
# Scan a directory (discovers files, detects duplicates, updates index)
python -X utf8 main.py scan <path> [--workers N] [--follow-symlinks] [--verbose]

# Show all duplicate groups
python -X utf8 main.py duplicates <path> [--workers N]

# Storage breakdown by category and extension
python -X utf8 main.py analyze <path>

# Top N largest files
python -X utf8 main.py largest <path> [--top N]

# Full combined report
python -X utf8 main.py report <path> [--workers N]

# Benchmark naive vs multi-stage
python -X utf8 main.py benchmark <path> [--workers 1,2,4,8]

# Preview cleanup (SAFE — never deletes)
python -X utf8 main.py clean <path> --dry-run

# Actual cleanup (requires confirmation)
python -X utf8 main.py clean <path> --no-dry-run

# SQLite index stats
python -X utf8 main.py index status
```

### Practical workflow

```bash
# Step 1: Scan a large directory
python -X utf8 main.py scan "C:\Users\YourName\Downloads" --workers 4

# Step 2: Inspect duplicates
python -X utf8 main.py duplicates "C:\Users\YourName\Downloads"

# Step 3: Preview what can be cleaned
python -X utf8 main.py clean "C:\Users\YourName\Downloads" --dry-run

# Step 4: Run again — much faster (incremental scan uses index)
python -X utf8 main.py scan "C:\Users\YourName\Downloads"

# Step 5: Benchmark on your dataset
python -X utf8 main.py benchmark "C:\Users\YourName\Downloads"
```

---

## 14. Interview Q&A

**Q: Why not just hash every file?**
> Naive hashing reads 100% of all data. FileSense's size filter eliminates
> unique-size files for free — no disk reads at all. On typical datasets,
> 60-80% of files have unique sizes. Partial hashing then eliminates most
> remaining non-duplicates with only 64 KB per file. Only true candidates
> reach full SHA-256 hashing. The benchmark command measures this empirically.

**Q: Why SHA-256 and not MD5?**
> MD5 has known collision vulnerabilities (two different files can produce the
> same MD5 hash). SHA-256 has no known practical collisions. For a deduplication
> tool that may be used to delete files, correctness is critical. The performance
> difference is negligible compared to disk I/O time.

**Q: How does incremental scanning work?**
> The persistent SQLite index stores `(path, size, mtime, partial_hash, full_hash)`.
> On rescan, `PersistentIndex.load_hashes()` compares current `size` and `mtime`
> against the stored values. If they match, the stored hashes are reused — no
> disk read needed. If they differ, the hashes are invalidated and recomputed.

**Q: Why SQLite and not a simple JSON file?**
> SQLite provides: (1) ACID transactions — no corruption on crash mid-write,
> (2) indexed lookups — O(log n) path queries, (3) WAL mode — concurrent read
> access during writes, (4) no external server. A JSON file would require
> loading the entire index into memory, lack atomic writes, and have O(n) lookup.

**Q: Why ThreadPoolExecutor and not ProcessPoolExecutor?**
> Hashing is I/O-bound. The GIL is released during `file.read()` (a system call),
> so threads can genuinely run in parallel during disk reads. `ProcessPoolExecutor`
> would add process spawn overhead (~100ms per worker) with no benefit.

**Q: What is the Strategy Pattern and why use it here?**
> Strategy defines a family of algorithms behind a common interface, making them
> interchangeable. `HashStrategy` is the interface; `PartialHashStrategy` and
> `FullHashStrategy` are the concrete strategies. Adding BLAKE3 hashing only
> requires a new class — no changes to `HashManager` or `DuplicateDetector`.
> This satisfies the Open/Closed Principle.

**Q: How would you scale FileSense to handle 10 million files?**
> Current bottlenecks: (1) `list[FileMetadata]` in memory — replace with
> generator-based streaming through the pipeline, (2) SQLite — consider
> chunked batch upserts, (3) single machine disk I/O — parallel workers per
> disk/partition. The pipeline architecture already supports this — each stage
> is independent and could process data in chunks.

**Q: How do you ensure no files are accidentally deleted?**
> Three layers: (1) `clean` defaults to `--dry-run=True` — deletion code path
> is never reached, (2) `--no-dry-run` must be explicitly set to reach deletion
> code, (3) `Confirm.ask("Continue?", default=False)` — the user must type `y`,
> the default response is `n`. Files are only opened in read-only mode during
> analysis (`open(path, "rb")`).
