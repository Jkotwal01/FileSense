"""DuplicateDetector — multi-stage pipeline for exact duplicate detection.

Pipeline:
  1. Group files by size              → skip unique sizes (no I/O)
  2. Compute partial hash (concurrent)
  3. Group by partial hash            → skip unique partial hashes
  4. Compute full SHA-256 (concurrent)
  5. Group by full hash               → final duplicate groups

Each stage eliminates non-candidates before the next (more expensive) stage.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from filesense.hasher import HashManager
from filesense.metadata import FileMetadata

logger = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """Represents a group of files sharing the same full SHA-256 hash."""

    full_hash: str
    files: list[FileMetadata] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def file_size(self) -> int:
        """Size of a single copy (all files in the group have the same size)."""
        return self.files[0].size if self.files else 0

    @property
    def total_size(self) -> int:
        """Total bytes consumed by all copies."""
        return self.file_size * self.file_count

    @property
    def wasted_size(self) -> int:
        """Bytes that could be freed by keeping only one copy."""
        return self.file_size * (self.file_count - 1)

    @property
    def original_candidate(self) -> FileMetadata:
        """Heuristic: shortest path is most likely the 'original'."""
        return min(self.files, key=lambda f: len(f.path))

    @property
    def duplicates(self) -> list[FileMetadata]:
        """All files except the original candidate."""
        orig = self.original_candidate
        return [f for f in self.files if f.path != orig.path]


class DuplicateDetector:
    """
    Identifies exact duplicate files using the multi-stage pipeline.

    Responsibilities:
      - Stage 1: Size grouping (free — no disk reads)
      - Stage 2: Partial hashing (concurrent, minimal I/O)
      - Stage 3: Full hashing (concurrent, streaming)
      - Return list of DuplicateGroup objects
    """

    def __init__(self, workers: int = 4) -> None:
        self.workers = max(1, workers)
        self._hasher = HashManager()

    def detect(self, files: list[FileMetadata]) -> list[DuplicateGroup]:
        """
        Run the full duplicate detection pipeline.

        Args:
            files: All FileMetadata objects from a scan (hashes may already
                   be populated from the persistent index).

        Returns:
            List of DuplicateGroup objects (only groups with ≥ 2 files).
        """
        logger.info("Duplicate detection started: %d files", len(files))

        # Stage 1: size grouping (free)
        size_candidates = self._group_by_size(files)
        logger.info(
            "After size filter: %d candidate files in %d groups",
            sum(len(g) for g in size_candidates.values()),
            len(size_candidates),
        )

        # Stage 2: partial hashing
        flat_candidates = [f for group in size_candidates.values() for f in group]
        self._hash_concurrent(flat_candidates, stage="partial")

        partial_candidates = self._group_by_partial_hash(flat_candidates)
        logger.info(
            "After partial hash filter: %d candidate files in %d groups",
            sum(len(g) for g in partial_candidates.values()),
            len(partial_candidates),
        )

        # Stage 3: full hashing
        flat_full = [f for group in partial_candidates.values() for f in group]
        self._hash_concurrent(flat_full, stage="full")

        duplicate_groups = self._build_duplicate_groups(flat_full)
        logger.info(
            "Duplicate detection complete: %d duplicate groups found",
            len(duplicate_groups),
        )
        return duplicate_groups

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _group_by_size(self, files: list[FileMetadata]) -> dict[int, list[FileMetadata]]:
        """Group files by size; return only groups with ≥ 2 files."""
        size_map: dict[int, list[FileMetadata]] = defaultdict(list)
        for meta in files:
            size_map[meta.size].append(meta)
        return {size: group for size, group in size_map.items() if len(group) >= 2}

    def _group_by_partial_hash(
        self, files: list[FileMetadata]
    ) -> dict[str, list[FileMetadata]]:
        """Group files by partial hash; return only groups with ≥ 2 files."""
        hash_map: dict[str, list[FileMetadata]] = defaultdict(list)
        for meta in files:
            if meta.partial_hash:
                hash_map[meta.partial_hash].append(meta)
        return {h: group for h, group in hash_map.items() if len(group) >= 2}

    def _build_duplicate_groups(
        self, files: list[FileMetadata]
    ) -> list[DuplicateGroup]:
        """Group files by full hash; build DuplicateGroup objects (≥ 2 files)."""
        hash_map: dict[str, list[FileMetadata]] = defaultdict(list)
        for meta in files:
            if meta.full_hash:
                hash_map[meta.full_hash].append(meta)

        groups = []
        for full_hash, group_files in hash_map.items():
            if len(group_files) >= 2:
                groups.append(DuplicateGroup(full_hash=full_hash, files=group_files))

        # Sort by wasted space descending (most impactful first)
        groups.sort(key=lambda g: g.wasted_size, reverse=True)
        return groups

    # ------------------------------------------------------------------
    # Concurrent hashing
    # ------------------------------------------------------------------

    def _hash_concurrent(
        self, files: list[FileMetadata], stage: str
    ) -> None:
        """
        Hash all files in parallel using a ThreadPoolExecutor.

        Skips files that already have their hash populated (index cache hit).
        Mutates FileMetadata objects in-place.

        Args:
            files: List of FileMetadata objects to hash.
            stage: 'partial' or 'full'
        """
        if stage == "partial":
            needs_hash = [f for f in files if not f.partial_hash]
            hash_fn = self._hasher.compute_partial
        else:
            needs_hash = [f for f in files if not f.full_hash]
            hash_fn = self._hasher.compute_full

        if not needs_hash:
            logger.debug("All %s hashes already cached — skipping", stage)
            return

        logger.info(
            "Hashing %d files (%s stage) with %d workers",
            len(needs_hash), stage, self.workers,
        )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(hash_fn, meta): meta for meta in needs_hash}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    meta = futures[future]
                    logger.error("Hash failed for %s: %s", meta.path, exc)
