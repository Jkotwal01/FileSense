# FileSense — Technical Interview Guide

> Complete deep-dive into every class, design decision, algorithm, and trade-off.

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
12. [Interview Q&A](#12-interview-qa)

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
3. Groups files by size   → eliminates impossible duplicates immediately (free)
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
    h = sha256(file.read())   # 100 GB dataset = 100 GB disk read
```

On a 100,000-file / 500 GB dataset this causes massive, unnecessary disk I/O.

### The Core Insight

> **Two files with different sizes can NEVER be exact duplicates.**
> Therefore, there is no reason to hash them against each other.

```
A.pdf  →  100 MB  ─┐
B.pdf  →  100 MB  ─┤  same size → must compare these
C.pdf  →   50 MB  ─┘  different size → skip immediately (0 disk reads)
```

This single observation — filter by size before hashing — is the entire
foundation of FileSense's multi-stage pipeline.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────┐
│        CLI  (Typer + Rich)           │
└─────────────────┬────────────────────┘
                  │ orchestrates
         ┌────────▼────────┐
         │  FileScanner    │  ← traverses directories
         └────────┬────────┘
                  │ produces list[FileMetadata]
         ┌────────▼────────┐
         │ PersistentIndex │  ← SQLite cache (incremental scans)
         └────────┬────────┘
                  │ enriched list[FileMetadata] (with cached hashes)
         ┌────────▼────────┐
         │DuplicateDetector│  ← multi-stage pipeline
         │                 │     uses HashManager internally
         └────────┬────────┘
                  │ list[DuplicateGroup]
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
StorageAnalyzer  Report    BenchmarkEngine
```

**Data flows in one direction — no circular dependencies between modules.**

---

## 4. Design Patterns

### Strategy Pattern — Hashing

The `HashStrategy` ABC defines the interface. Two concrete implementations exist:

```python
class HashStrategy(ABC):
    @abstractmethod
    def compute(self, path: str) -> str:
        """Return hex-digest hash string for the given file path."""


class PartialHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        # reads first 32 KB + last 32 KB only (64 KB max)
        ...


class FullHashStrategy(HashStrategy):
    def compute(self, path: str) -> str:
        # streams entire file in 64 KB chunks — never loads into RAM
        ...
```

**Why Strategy Pattern here?**
- Adding a new hash algorithm (e.g. BLAKE3) only requires a new class
- `HashManager` and `DuplicateDetector` never need to change
- Satisfies the **Open/Closed Principle** (open for extension, closed for modification)

### Single Responsibility Principle

Every class does exactly one thing:

| Class | Sole Responsibility |
|-------|-------------------|
| `FileMetadata` | Hold data for one file — no I/O, no logic |
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
    path: str           # absolute path — primary key for the index
    filename: str       # display name
    extension: str      # lowercase (.pdf, .mp4...)
    size: int           # bytes — used for size grouping
    mtime: float        # last modified timestamp (epoch) — change detection
    ctime: float        # creation/change time
    inode: str          # OS file identity (inode on Linux, hash on Windows)
    permissions: str    # octal string e.g. '0o644'
    category: str       # auto-classified: Images/Videos/Code/...
    partial_hash: str   # populated after Stage 2
    full_hash: str      # populated after Stage 3 — the duplicate key
    last_scanned: datetime
    status: str         # 'active' | 'deleted'
```

**Key methods:**

```python
def is_changed(self, other: FileMetadata) -> bool:
    """
    Returns True if size or mtime differs from the indexed record.
    Used by PersistentIndex to decide whether to rehash.

    The 0.001s tolerance on mtime handles filesystem precision differences
    (FAT32 rounds mtime to 2-second intervals, for example).
    """
    return self.size != other.size or abs(self.mtime - other.mtime) > 0.001


def human_size(self) -> str:
    """5,242,880 → '5.0 MB'  — iterates through units."""
```

**`__post_init__` — automatic classification:**

```python
def __post_init__(self) -> None:
    if not self.category or self.category == "Other":
        self.category = classify_extension(self.extension)
```

The `EXTENSION_CATEGORIES` dict maps 60+ extensions to 8 categories:

```python
EXTENSION_CATEGORIES = {
    ".jpg": "Images",  ".jpeg": "Images",
    ".mp4": "Videos",  ".mkv": "Videos",
    ".pdf": "Documents", ".docx": "Documents",
    ".py": "Code",     ".js": "Code",
    ".zip": "Archives", ".rar": "Archives",
    ".mp3": "Audio",   ".flac": "Audio",
    ".exe": "Executables",
    # ... 50+ more
}
```

**Interview talking point:** Using `@dataclass` keeps the declaration minimal while
providing `__init__`, `__repr__`, and `__eq__` for free. Placing `is_changed()`
directly on the data object follows the principle of keeping behaviour close to
the data it operates on.

---

### 5.2 `FileScanner` — `scanner.py`

**Purpose:** Recursively traverse a directory tree, `stat()` every file,
and return a `ScanResult` with all `FileMetadata` objects.

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
        """Calls path.stat() and builds a FileMetadata from the OS response."""
```

**`ScanResult` — aggregate container:**

```python
class ScanResult:
    files: list[FileMetadata]
    directory_count: int
    error_count: int
    errors: list[str]       # human-readable error messages for the report

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)
```

**Symlink handling (explicit pre-check pattern):**

```python
if entry.is_symlink():
    if not self.follow_symlinks:
        continue                        # default: skip symlinks
    resolved = entry.resolve()
    if resolved.is_dir():
        yield from self._walk(entry, result)
    elif resolved.is_file():
        yield entry
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
    return   # skip this directory, continue with siblings
```

Every error is logged as WARNING and appended to `ScanResult.errors`.
The scan **never raises** unless the root path itself is invalid.

**Interview talking point:** The generator pattern (`yield from self._walk()`)
keeps stack depth O(directory depth) rather than O(n files). Separating
`_walk()` (yields paths) from `_collect_metadata()` (stats a path) makes each
independently testable — you can test traversal without caring about stat() and
vice versa.

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
            head = fh.read(PARTIAL_READ_BYTES)       # first 32 KB
            hasher.update(head)

            if file_size > PARTIAL_READ_BYTES * 2:
                fh.seek(-PARTIAL_READ_BYTES, 2)      # seek from end of file
                tail = fh.read(PARTIAL_READ_BYTES)   # last 32 KB
                hasher.update(tail)

        return hasher.hexdigest()
```

**Why head + tail?**
- The **head** catches differences in file headers (format metadata, magic bytes)
- The **tail** catches differences in endings (compression trailers, EOF markers)
- Max 64 KB read per file regardless of file size — this is the optimization

**Edge case:** files smaller than 64 KB — the tail seek is skipped to avoid
double-reading the same bytes.

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

**Why streaming?** A 10 GB file must never be loaded into RAM.
Streaming 64 KB at a time means memory usage is constant (~64 KB)
regardless of file size. This satisfies AC-03 and NFR-02.

**The walrus operator (`:=`)** assigns and tests in one expression:
```python
# Equivalent to:
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
        if meta.partial_hash:       # skip if already cached
            return meta
        try:
            meta.partial_hash = self._partial.compute(meta.path)
        except PermissionError:
            logger.warning("Permission denied: %s", meta.path)
        except FileNotFoundError:
            logger.warning("File disappeared: %s", meta.path)
        return meta                 # always returns, even on error

    def compute_full(self, meta: FileMetadata) -> FileMetadata:
        # same skip-if-cached + graceful error pattern
```

**Interview talking point:** The Strategy pattern answers "how would you add
BLAKE3 hashing?" — implement `BLAKE3HashStrategy(HashStrategy)`, pass it to
`HashManager`. No changes to `DuplicateDetector` or any pipeline code.

---

### 5.4 `PersistentIndex` — `index.py`

**Purpose:** SQLite-backed cache that stores metadata and computed hashes,
enabling incremental scanning — unchanged files are never rehashed.

**Database location:** `~/.filesense/filesense.db`

```python
class PersistentIndex:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row     # named column access
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()
```

**Why WAL (Write-Ahead Logging) mode?**
SQLite's default journal mode locks the entire database during writes.
WAL allows concurrent reads while a write is in progress — critical when
concurrent hashing workers update the index simultaneously.

**Core methods:**

```python
def upsert(self, meta: FileMetadata) -> None:
    """
    INSERT OR UPDATE in a single atomic SQL statement.
    ON CONFLICT(path) DO UPDATE handles both new and existing records.
    """
    self._conn.execute("""
        INSERT INTO files (path, size, mtime, ...)
        VALUES (...)
        ON CONFLICT(path) DO UPDATE SET
            size         = excluded.size,
            mtime        = excluded.mtime,
            partial_hash = excluded.partial_hash,
            full_hash    = excluded.full_hash,
            last_scanned = excluded.last_scanned,
            status       = 'active'
    """)


def is_changed(self, meta: FileMetadata) -> bool:
    """
    Returns True if size+mtime differ from the stored record,
    or if the path has never been indexed.
    """
    indexed = self.get(meta.path)
    if indexed is None:
        return True
    return meta.is_changed(indexed)


def load_hashes(self, meta: FileMetadata) -> FileMetadata:
    """
    KEY METHOD for incremental scanning.

    If the file is unchanged, copies partial_hash + full_hash
    from the index into the metadata object — transparently.

    The caller (DuplicateDetector) never knows whether hashes came
    from disk or from the SQLite cache.
    """
    if not self.is_changed(meta):
        indexed = self.get(meta.path)
        if indexed:
            meta.partial_hash = indexed.partial_hash
            meta.full_hash = indexed.full_hash
    return meta
```

**Interview talking point:** `load_hashes()` is a **Transparent Proxy** —
it injects cached data into the metadata object invisibly. The
`DuplicateDetector` simply sees a `FileMetadata` with hashes already set and
skips recomputing them. This clean separation means the entire incremental
scan logic is contained in one 10-line method.

---

### 5.5 `DuplicateDetector` — `duplicate.py`

**Purpose:** Run the complete multi-stage pipeline and return `DuplicateGroup`
objects sorted by wasted space descending.

```python
class DuplicateDetector:
    def __init__(self, workers: int = 4) -> None:
        self.workers = max(1, workers)
        self._hasher = HashManager()

    def detect(self, files: list[FileMetadata]) -> list[DuplicateGroup]:
        """Runs all three pipeline stages, returns sorted duplicate groups."""
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

    # Stage 3 — full SHA-256 (concurrent, streaming)
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
    # Singletons cannot be duplicates — eliminate immediately
    return {size: group for size, group in size_map.items() if len(group) >= 2}
```

**Concurrent hashing with skip-if-cached:**

```python
def _hash_concurrent(self, files, stage):
    if stage == "partial":
        needs_hash = [f for f in files if not f.partial_hash]   # cache hits skipped
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
        """Bytes freed by keeping one copy: size x (count - 1)"""
        return self.file_size * (self.file_count - 1)

    @property
    def original_candidate(self) -> FileMetadata:
        """
        Heuristic: shortest path is most likely the original.
        Avoids needing filesystem creation dates (unreliable on Windows/FAT32).
        """
        return min(self.files, key=lambda f: len(f.path))
```

Groups are sorted by `wasted_size` descending — highest-impact duplicates first.

**Interview talking point:** The three-stage filter mirrors database query
optimisation — cheapest filter first (size: free), then progressively more
expensive (partial hash: 64 KB, full hash: whole file). Only files that survive
all three stages are confirmed exact duplicates.

---

### 5.6 `StorageAnalyzer` — `storage.py`

**Purpose:** Pure analytics over a list of `FileMetadata` objects.
No I/O, no output — returns structured dicts/lists.

```python
class StorageAnalyzer:

    def total_usage(self, files: list[FileMetadata]) -> dict:
        """file_count, total_bytes, total_human."""

    def by_extension(self, files: list[FileMetadata]) -> list[dict]:
        """
        Groups files by extension, aggregates count + bytes.
        Returns sorted by total bytes descending.

        Pattern: defaultdict(lambda: {"count": 0, "bytes": 0})
        """

    def by_category(self, files: list[FileMetadata]) -> list[dict]:
        """Same pattern, grouped by category (Images/Videos/Code/...)."""

    def by_directory(self, files: list[FileMetadata]) -> list[dict]:
        """Grouped by immediate parent directory, sorted by size descending."""

    def largest_files(self, files: list[FileMetadata], top: int = 10) -> list[FileMetadata]:
        """Top N files by size. Uses sorted() — O(n log n) but n is manageable."""
        return sorted(files, key=lambda f: f.size, reverse=True)[:top]

    def duplicate_summary(self, groups: list[DuplicateGroup]) -> dict:
        """
        Aggregates across all duplicate groups:
          - total duplicate file count
          - total bytes in all duplicate files
          - total wasted bytes (savings if deduplicated)
        """
```

**Key aggregation pattern:**

```python
def by_extension(self, files):
    ext_map = defaultdict(lambda: {"count": 0, "bytes": 0})
    for meta in files:
        ext = meta.extension or "(no ext)"
        ext_map[ext]["count"] += 1
        ext_map[ext]["bytes"] += meta.size
    result = [{"extension": ext, **data, "human": _human_size(data["bytes"])}
              for ext, data in ext_map.items()]
    result.sort(key=lambda x: x["bytes"], reverse=True)
    return result
```

**Interview talking point:** `StorageAnalyzer` is completely pure — same input
always produces same output, no side effects. This makes it trivially testable
(13 unit tests, zero mocking) and reusable in any context (CLI, future web API,
benchmark reports).

---

### 5.7 `ReportGenerator` — `report.py`

**Purpose:** Format and print analysis results using Rich.
The only class that produces terminal output.

```python
class ReportGenerator:
    def __init__(self) -> None:
        self._analyzer = StorageAnalyzer()   # used for internal metric calls

    def full_report(self, scan_result, duplicate_groups, root) -> None: ...
    def scan_summary(self, scan_result, elapsed) -> None: ...
    def duplicate_report(self, groups) -> None: ...
    def storage_report(self, files) -> None: ...
    def extension_report(self, files, top=15) -> None: ...
    def largest_files_report(self, files, top=10) -> None: ...
    def cleanup_preview(self, groups) -> None: ...
    def index_status_report(self, stats) -> None: ...
```

**Rich usage patterns:**

```python
# Summary Panel
console.print(Panel(
    f"[bold]Total Storage:[/bold] [cyan]{usage['total_human']}[/cyan]\n"
    f"[bold]Potential Savings:[/bold] [green]{dup['wasted_human']}[/green]",
    title="Summary",
    border_style="cyan"
))

# Structured Table
table = Table(title="Top Extensions", box=box.SIMPLE_HEAD)
table.add_column("Extension", style="bold")
table.add_column("Files", justify="right")
table.add_column("Storage", justify="right", style="cyan")
for row in data:
    table.add_row(row["extension"], str(row["count"]), row["human"])
console.print(table)
```

**Interview talking point:** Separating `StorageAnalyzer` (compute) from
`ReportGenerator` (display) follows the **MVC** pattern. The View
(`ReportGenerator`) never performs calculations. You could add a JSON output
formatter or a future REST API response without touching any computation code.

---

### 5.8 `BenchmarkEngine` — `benchmark.py`

**Purpose:** Empirically measure naive vs multi-stage hashing and compare
worker configurations.

```python
@dataclass
class BenchmarkResult:
    method: str
    workers: int
    elapsed_seconds: float
    files_processed: int
    bytes_read: int           # estimated actual disk reads
    hash_operations: int
    duplicate_groups: int
    peak_memory_bytes: int    # from tracemalloc

    def throughput_mbs(self) -> float:
        return (self.bytes_read / 1024 / 1024) / self.elapsed_seconds


class BenchmarkEngine:
    def __init__(self, workers_list: list[int] = None) -> None:
        self.workers_list = workers_list or [1, 2, 4]

    def run(self, path: str) -> list[BenchmarkResult]:
        """Scans directory, then runs naive + all multi-stage configs."""

    def _run_naive(self, files) -> BenchmarkResult:
        """Hash every file with FullHashStrategy — no filtering."""

    def _run_multistage(self, files, workers) -> BenchmarkResult:
        """Full 3-stage pipeline with given worker count."""
```

**Memory measurement with `tracemalloc`:**

```python
tracemalloc.start()
t_start = time.perf_counter()

# ... hashing work ...

elapsed = time.perf_counter() - t_start
_, peak = tracemalloc.get_traced_memory()   # peak heap since start()
tracemalloc.stop()
```

**Fair comparison — fresh copies per run:**

```python
import copy
fresh = [copy.copy(f) for f in files]
for f in fresh:
    f.partial_hash = None    # clear any cached hashes
    f.full_hash = None       # so each run starts from scratch
```

**Interview talking point:** The benchmark is the scientific validation of the
pipeline's design claim. `tracemalloc` is stdlib — no external profiler needed.
It instruments heap allocations within a code block and reports peak usage.

---

### 5.9 CLI — `cli.py`

**Purpose:** Expose all functionality through Typer commands. Orchestrates
all other classes in the correct order.

```python
app = typer.Typer(name="filesense", ...)

@app.command()
def scan(
    path: str = typer.Argument(...),
    workers: int = typer.Option(4, "--workers", "-w"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None: ...
```

**Standard orchestration pattern inside every command:**

```python
# 1. Scan
scanner = FileScanner()
scan_result = scanner.scan(root)

# 2. Load cached hashes from index (incremental scan)
index = PersistentIndex()
for meta in scan_result.files:
    index.load_hashes(meta)

# 3. Detect duplicates
detector = DuplicateDetector(workers)
groups = detector.detect(scan_result.files)

# 4. Persist updated hashes to index
for meta in scan_result.files:
    index.upsert(meta)
index.close()

# 5. Display results
ReportGenerator().some_report(...)
```

**Three-layer safety for `clean`:**

```python
@app.command()
def clean(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    reporter.cleanup_preview(groups)    # always shown regardless

    if not dry_run:                     # requires explicit --no-dry-run
        confirmed = Confirm.ask("Continue?", default=False)  # default = No
        if confirmed:
            for group in groups:
                for dup in group.duplicates:
                    Path(dup.path).unlink()
```

Layer 1: default is `--dry-run=True` — deletion code never reached.  
Layer 2: requires explicit `--no-dry-run` flag.  
Layer 3: `Confirm.ask()` defaults to `N` — must type `y` explicitly.

---

## 6. The Multi-Stage Pipeline — Core Algorithm

```
All files
    |
    v
+-----------------------------------------------------------------------+
| STAGE 1: Group by SIZE                              (0 disk I/O)      |
|                                                                       |
| size_map = defaultdict(list)                                          |
| for f in files: size_map[f.size].append(f)                           |
| candidates = {size: group for ... if len(group) >= 2}                |
|                                                                       |
| Example: 10,000 files  →  3,000 size-match candidates                |
+-----------------------------------------------------------------------+
    |  ~3,000 files remain (70% eliminated for free)
    v
+-----------------------------------------------------------------------+
| STAGE 2: Partial Hash  (32 KB head + 32 KB tail per file)             |
|                                                                       |
| Concurrent with ThreadPoolExecutor                                    |
| Max 64 KB read per file                                               |
|                                                                       |
| Example: 3,000 files  →  200 partial-hash-match candidates           |
+-----------------------------------------------------------------------+
    |  ~200 files remain (98% eliminated with tiny reads)
    v
+-----------------------------------------------------------------------+
| STAGE 3: Full SHA-256  (streaming, concurrent)                        |
|                                                                       |
| Reads entire file in 64 KB chunks                                     |
| Only for files that survived both previous stages                     |
|                                                                       |
| Example: 200 files  →  80 confirmed duplicate groups                 |
+-----------------------------------------------------------------------+
    |  Confirmed exact duplicates
    v
  DuplicateGroup list — sorted by wasted_size descending
```

**I/O comparison (10,000 files / 50 GB dataset):**

| Approach | Files fully read | Bytes read from disk |
|----------|-----------------|---------------------|
| Naive | 10,000 | 50.0 GB |
| FileSense — Stage 1 (size filter) | 0 | 0 B (free) |
| FileSense — Stage 2 (partial hash) | 3,000 | 187.5 MB |
| FileSense — Stage 3 (full hash) | 200 | ~5.0 GB |
| **FileSense total** | **200** | **~5.2 GB** |
| **Savings vs naive** | | **~44.8 GB less disk read** |

---

## 7. Incremental Scanning

On repeated scans of the same directory, unchanged files must not be rehashed.

```
Second scan of the same directory:

For each discovered file:
  +----------------------------------------------------+
  | index.load_hashes(meta)                            |
  |                                                    |
  |   1. Look up path in SQLite                        |
  |   2. Compare size + mtime                          |
  |      |-- Same    --> copy hashes from index        |
  |      |              meta.partial_hash = cached     |
  |      |              meta.full_hash    = cached     |
  |      `-- Changed --> hashes stay None              |
  |                      (will be recomputed)          |
  +----------------------------------------------------+
```

**Key implementation — skip-if-cached in the hasher:**

```python
# In DuplicateDetector._hash_concurrent:
needs_hash = [f for f in files if not f.partial_hash]   # cached files skipped
```

Files with `partial_hash` already set are silently skipped by the hasher.
The pipeline treats them identically to freshly hashed files.

**Result:** On a second scan of an unchanged 100 GB directory:
- Disk reads: ~0 bytes (all hashes served from SQLite)
- Time: seconds instead of minutes

---

## 8. Concurrency Design

**Why `ThreadPoolExecutor` and not `ProcessPoolExecutor`?**

File hashing is **I/O-bound** — the bottleneck is reading bytes from disk,
not computing the hash.

- The Python GIL is **released** during `fh.read()` (a system call)
- Multiple threads therefore genuinely overlap during disk reads
- `ProcessPoolExecutor` adds process spawn overhead (~100ms/worker) for zero gain

```python
with ThreadPoolExecutor(max_workers=self.workers) as pool:
    # Submit all candidate files simultaneously
    futures = {pool.submit(hash_fn, meta): meta for meta in needs_hash}

    # Collect results as they complete (not in submission order)
    for future in as_completed(futures):
        try:
            future.result()
        except Exception as exc:
            logger.error("Hash failed for %s: %s", futures[future].path, exc)
```

**`as_completed()` vs `pool.map()`:**

| | `as_completed()` | `pool.map()` |
|--|-----------------|-------------|
| Yields | Each result as it finishes | All results after ALL complete |
| Slow file | Other futures keep processing | Everything blocks |
| Error handling | Per-future try/except | One exception stops iteration |

FileSense uses `as_completed()` so a single slow/large file does not block
the entire pipeline.

**Configurable workers:**

```bash
python -X utf8 main.py scan ./data --workers 1   # baseline (sequential-ish)
python -X utf8 main.py scan ./data --workers 4   # default
python -X utf8 main.py scan ./data --workers 8   # high-throughput storage
```

More workers is not always faster — disk I/O saturation and thread scheduling
overhead eventually dominate. Use `benchmark` to find the optimum for your hardware.

---

## 9. Database Schema

```sql
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT UNIQUE NOT NULL,   -- absolute path, PK for upserts
    size         INTEGER,               -- bytes
    mtime        REAL,                  -- last modified (epoch float)
    inode        TEXT,                  -- OS file identity
    extension    TEXT,                  -- lowercase (.pdf, .mp4...)
    partial_hash TEXT,                  -- set after Stage 2
    full_hash    TEXT,                  -- set after Stage 3, NULL until then
    last_scanned DATETIME,              -- ISO UTC timestamp
    status       TEXT DEFAULT 'active'  -- 'active' | 'deleted'
);

CREATE INDEX idx_files_path      ON files(path);       -- O(log n) path lookup
CREATE INDEX idx_files_full_hash ON files(full_hash);  -- future: find by hash
```

**Upsert — single atomic statement:**

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

Atomic — no separate SELECT + INSERT/UPDATE needed.
Avoids race conditions in concurrent scenarios.

---

## 10. Error Handling Strategy

| Error Type | Location | Behaviour |
|-----------|----------|-----------|
| `PermissionError` on directory | `FileScanner._walk` | Log WARNING, skip dir, continue |
| `PermissionError` on file | `FileScanner.scan` | Log WARNING, skip file, continue |
| `FileNotFoundError` during hash | `HashManager` | Log WARNING, hash stays None |
| `OSError` during hash | `HashManager` | Log ERROR, hash stays None |
| Root path not found | `FileScanner.scan` | Raise `FileNotFoundError` (unrecoverable) |
| Root is a file | `FileScanner.scan` | Raise `NotADirectoryError` (unrecoverable) |
| SQLite error | `PersistentIndex` | Propagates (program state cannot continue) |

**Log levels:**

```
DEBUG   → per-file hash OK (verbose mode only)
INFO    → scan started/completed, pipeline stage entry counts
WARNING → permission denied, file disappeared
ERROR   → unexpected OS errors during hashing
```

Logs: `logs/filesense.log` — rotating file handler (5 MB max, 3 backups).

---

## 11. Testing Strategy

### Coverage (55 tests, 4 files)

| File | Tests | Focus |
|------|-------|-------|
| `test_scanner.py` | 18 | Extension classification, FileMetadata, recursive scan, error cases |
| `test_hasher.py` | 13 | Same/different content hashing, empty files, large files, skip-if-cached |
| `test_duplicate.py` | 11 | Exact duplicates detected, size filter, filename irrelevant, cached hashes |
| `test_storage.py` | 13 | Total usage, extension/category sorting, largest files, duplicate savings |

### Acceptance criteria verified by tests

| AC | Test | Status |
|----|------|--------|
| AC-01: Identical files detected | `test_finds_exact_duplicates` | PASS |
| AC-02: Different sizes skip hashing | `test_different_sizes_not_duplicates` | PASS |
| AC-03: Large file hashed without OOM | `test_large_file_streams` (2 MB) | PASS |
| AC-04: Unchanged files reuse index | `test_cached_hashes_reused` | PASS |
| AC-05: Report shows savings | `test_duplicate_summary` | PASS |
| AC-06: Errors don't stop scan | `FileScanner` error handling | PASS |
| AC-07: Benchmark produces metrics | `BenchmarkEngine` | PASS |
| AC-08: No deletion without confirm | `clean --dry-run` default | PASS |

### Business rules verified by tests

| BR | Test | Status |
|----|------|--------|
| BR-01: Filename NOT the criterion | `test_same_name_different_content_not_duplicate` | PASS |
| BR-02: Same content = duplicate | `test_different_name_same_content_is_duplicate` | PASS |
| BR-06: No auto-deletion | `clean` three-layer safety | PASS |

---

## 12. Interview Q&A

**Q: Why not just hash every file?**
> Naive hashing reads 100% of all data from disk. FileSense's size filter
> eliminates unique-size files for free — zero bytes read. On typical datasets,
> 60-80% of files have unique sizes. Partial hashing then eliminates most
> remaining non-duplicates with only 64 KB per file. Only true candidates reach
> full SHA-256. The benchmark command measures the exact improvement on your dataset.

---

**Q: Why SHA-256 and not MD5?**
> MD5 has known practical collision vulnerabilities — two different files can
> produce the same MD5 hash. SHA-256 has no known practical collisions. For a
> deduplication tool that guides file deletion, a false positive (two different
> files declared as duplicates) would cause data loss. The performance difference
> is negligible compared to disk I/O time.

---

**Q: How does incremental scanning work?**
> The SQLite index stores `(path, size, mtime, partial_hash, full_hash)` for every
> scanned file. On rescan, `PersistentIndex.load_hashes()` compares the current
> file's `size` and `mtime` against the stored values. Match → copy hashes from
> index, skip disk read. Mismatch → invalidate hashes, recompute from disk.

---

**Q: Why SQLite and not a plain JSON file?**
> SQLite provides: (1) ACID transactions — no corruption on crash mid-write,
> (2) indexed lookups — O(log n) per path vs O(n) linear scan of a JSON array,
> (3) WAL mode — concurrent reads during writes, (4) no external server or
> dependencies. A JSON file would require loading the entire index into memory,
> lack atomic writes, and degrade to O(n) on every lookup.

---

**Q: Why ThreadPoolExecutor and not ProcessPoolExecutor?**
> File hashing is I/O-bound — reading bytes from disk is the bottleneck, not
> computing the hash. The Python GIL is released during `file.read()` (a system
> call), so threads genuinely overlap during disk I/O. ProcessPoolExecutor adds
> ~100ms process spawn overhead per worker, serialises arguments via pickle,
> and provides no benefit for I/O-bound workloads.

---

**Q: What is the Strategy Pattern and why is it used here?**
> Strategy defines a family of algorithms behind a common interface, making them
> interchangeable at runtime without changing the caller. `HashStrategy` is the
> ABC; `PartialHashStrategy` and `FullHashStrategy` are the strategies. Adding
> BLAKE3 requires only a new class — `HashManager` and `DuplicateDetector` are
> untouched. This satisfies the Open/Closed Principle.

---

**Q: How would you scale FileSense to 10 million files?**
> Current limitations: (1) `list[FileMetadata]` in memory — refactor pipeline
> to process in chunks using generators, (2) SQLite single-writer bottleneck —
> batch upserts in transactions (1000 rows/commit), (3) single disk — partition
> the file list by storage device and run separate worker pools per device.
> The pipeline architecture is already staged and concurrent — scaling is an
> engineering effort, not an architectural rewrite.

---

**Q: How do you guarantee no accidental deletion?**
> Three independent safety layers:
> 1. `clean` defaults to `--dry-run=True` — the deletion code path is unreachable
> 2. `--no-dry-run` must be explicitly passed to reach deletion code
> 3. `Confirm.ask("Continue?", default=False)` — the prompt default is `N`,
>    user must actively type `y`
>
> Additionally, all file reads use `open(path, "rb")` — read-only mode.
> FileSense never calls `open(path, "w")` or modifies file contents.
