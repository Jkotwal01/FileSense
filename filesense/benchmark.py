"""BenchmarkEngine — compares naive full hashing vs multi-stage hashing.

Measures:
  - Execution time
  - Files processed
  - Bytes read (estimated)
  - Hashing operations performed
  - Duplicate groups found
  - Memory usage (via tracemalloc)

Compares worker counts: 1, 2, 4, 8 for the multi-stage approach.
"""

import logging
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from collections import defaultdict

from filesense.hasher import FullHashStrategy, HashManager
from filesense.metadata import FileMetadata
from filesense.scanner import FileScanner
from filesense.storage import _human_size

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Stores metrics for a single benchmark run."""

    method: str
    workers: int
    elapsed_seconds: float
    files_processed: int
    bytes_read: int            # estimated bytes actually read from disk
    hash_operations: int       # number of hash computations performed
    duplicate_groups: int
    peak_memory_bytes: int     # peak RAM from tracemalloc

    def throughput_mbs(self) -> float:
        """MB/s of data processed."""
        if self.elapsed_seconds == 0:
            return 0.0
        return (self.bytes_read / 1024 / 1024) / self.elapsed_seconds

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "workers": self.workers,
            "elapsed_s": round(self.elapsed_seconds, 3),
            "files": self.files_processed,
            "bytes_read": self.bytes_read,
            "bytes_read_human": _human_size(self.bytes_read),
            "hash_ops": self.hash_operations,
            "dup_groups": self.duplicate_groups,
            "peak_mem_human": _human_size(self.peak_memory_bytes),
            "throughput_mbs": round(self.throughput_mbs(), 2),
        }


class BenchmarkEngine:
    """
    Benchmarks naive vs multi-stage hashing strategies on a given dataset.

    Naive  : compute full SHA-256 for every single file.
    Multi  : size filter → partial hash → full SHA-256 (only candidates).
    """

    def __init__(self, workers_list: list[int] | None = None) -> None:
        self.workers_list = workers_list or [1, 2, 4]

    def run(self, path: str) -> list[BenchmarkResult]:
        """
        Scan the directory, then run all benchmark strategies.

        Returns:
            List of BenchmarkResult — one for naive, one per worker config.
        """
        logger.info("Benchmark scan: %s", path)
        scanner = FileScanner()
        scan_result = scanner.scan(path)
        files = scan_result.files

        if not files:
            logger.warning("No files found in %s — nothing to benchmark", path)
            return []

        logger.info("Benchmark dataset: %d files", len(files))
        results: list[BenchmarkResult] = []

        # Run naive full hashing
        results.append(self._run_naive(files))

        # Run multi-stage with different worker counts
        for workers in self.workers_list:
            results.append(self._run_multistage(files, workers))

        return results

    # ------------------------------------------------------------------
    # Benchmark strategies
    # ------------------------------------------------------------------

    def _run_naive(self, files: list[FileMetadata]) -> BenchmarkResult:
        """Naive: hash every file completely, no filtering."""
        logger.info("Running naive benchmark (%d files)...", len(files))
        strategy = FullHashStrategy()
        hash_ops = 0
        bytes_read = 0

        tracemalloc.start()
        t_start = time.perf_counter()

        hash_map: dict[str, list[str]] = defaultdict(list)
        for meta in files:
            try:
                h = strategy.compute(meta.path)
                hash_map[h].append(meta.path)
                hash_ops += 1
                bytes_read += meta.size
            except OSError:
                pass

        elapsed = time.perf_counter() - t_start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        dup_groups = sum(1 for paths in hash_map.values() if len(paths) >= 2)

        return BenchmarkResult(
            method="Naive (full hash all)",
            workers=1,
            elapsed_seconds=elapsed,
            files_processed=len(files),
            bytes_read=bytes_read,
            hash_operations=hash_ops,
            duplicate_groups=dup_groups,
            peak_memory_bytes=peak,
        )

    def _run_multistage(self, files: list[FileMetadata], workers: int) -> BenchmarkResult:
        """Multi-stage: size → partial hash → full hash (only real candidates)."""
        logger.info("Running multi-stage benchmark (workers=%d)...", workers)

        # Work on fresh copies (clear any cached hashes from prior runs)
        import copy
        fresh = [copy.copy(f) for f in files]
        for f in fresh:
            f.partial_hash = None
            f.full_hash = None

        hash_mgr = HashManager()
        hash_ops = 0
        bytes_read = 0

        tracemalloc.start()
        t_start = time.perf_counter()

        # Stage 1: group by size
        size_map: dict[int, list[FileMetadata]] = defaultdict(list)
        for meta in fresh:
            size_map[meta.size].append(meta)
        size_candidates = [m for g in size_map.values() if len(g) >= 2 for m in g]

        # Stage 2: partial hash
        partial_bytes = 32 * 1024 * 2  # 32KB head + 32KB tail
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(hash_mgr.compute_partial, m): m for m in size_candidates}
            for future in as_completed(futures):
                try:
                    future.result()
                    hash_ops += 1
                    bytes_read += min(futures[future].size, partial_bytes)
                except Exception:
                    pass

        # Group by partial hash
        partial_map: dict[str, list[FileMetadata]] = defaultdict(list)
        for meta in size_candidates:
            if meta.partial_hash:
                partial_map[meta.partial_hash].append(meta)
        partial_candidates = [m for g in partial_map.values() if len(g) >= 2 for m in g]

        # Stage 3: full hash
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(hash_mgr.compute_full, m): m for m in partial_candidates}
            for future in as_completed(futures):
                try:
                    future.result()
                    hash_ops += 1
                    bytes_read += futures[future].size
                except Exception:
                    pass

        # Count duplicate groups
        full_map: dict[str, list[str]] = defaultdict(list)
        for meta in partial_candidates:
            if meta.full_hash:
                full_map[meta.full_hash].append(meta.path)
        dup_groups = sum(1 for paths in full_map.values() if len(paths) >= 2)

        elapsed = time.perf_counter() - t_start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        return BenchmarkResult(
            method=f"Multi-stage",
            workers=workers,
            elapsed_seconds=elapsed,
            files_processed=len(files),
            bytes_read=bytes_read,
            hash_operations=hash_ops,
            duplicate_groups=dup_groups,
            peak_memory_bytes=peak,
        )
