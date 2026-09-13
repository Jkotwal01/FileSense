"""HashManager — multi-stage hashing engine using Strategy Pattern.

Stages:
  1. PartialHasher  → reads first 32 KB + last 32 KB (64 KB total)
  2. FullHasher     → streaming SHA-256 (never loads full file into RAM)

Design pattern: Strategy — HashStrategy ABC with two concrete implementations.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from filesense.metadata import FileMetadata

logger = logging.getLogger(__name__)

# Partial hash: read this many bytes from start AND from end of file
PARTIAL_READ_BYTES: int = 32 * 1024  # 32 KB

# Full hash: streaming chunk size
STREAM_CHUNK_BYTES: int = 64 * 1024  # 64 KB per read


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------

class HashStrategy(ABC):
    """Abstract base class for hashing strategies."""

    @abstractmethod
    def compute(self, path: str) -> str:
        """Compute and return a hex-digest hash string for the given file path."""


# ---------------------------------------------------------------------------
# Concrete strategies
# ---------------------------------------------------------------------------

class PartialHashStrategy(HashStrategy):
    """
    Reads only the first PARTIAL_READ_BYTES and last PARTIAL_READ_BYTES
    of a file to produce a quick discriminating hash.

    For files smaller than 2 × PARTIAL_READ_BYTES, the entire file is read
    (safe fallback — no double-reading for very small files).
    """

    def compute(self, path: str) -> str:
        hasher = hashlib.sha256()
        file_size = Path(path).stat().st_size

        with open(path, "rb") as fh:
            # Read from the start
            head = fh.read(PARTIAL_READ_BYTES)
            hasher.update(head)

            # Read from the end (skip if file is too small to have a distinct tail)
            if file_size > PARTIAL_READ_BYTES * 2:
                fh.seek(-PARTIAL_READ_BYTES, 2)  # seek from end
                tail = fh.read(PARTIAL_READ_BYTES)
                hasher.update(tail)

        return hasher.hexdigest()


class FullHashStrategy(HashStrategy):
    """
    Streams the entire file through SHA-256 in STREAM_CHUNK_BYTES chunks.
    Never loads the whole file into memory.
    """

    def compute(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            while chunk := fh.read(STREAM_CHUNK_BYTES):
                hasher.update(chunk)
        return hasher.hexdigest()


# ---------------------------------------------------------------------------
# HashManager — orchestrates the strategies
# ---------------------------------------------------------------------------

class HashManager:
    """
    Orchestrates hashing of FileMetadata objects.

    Uses PartialHashStrategy and FullHashStrategy internally.
    Updates the FileMetadata in-place and returns it.
    """

    def __init__(self) -> None:
        self._partial = PartialHashStrategy()
        self._full = FullHashStrategy()

    def compute_partial(self, meta: FileMetadata) -> FileMetadata:
        """
        Compute and store the partial hash on the metadata object.

        Returns the same metadata object (mutated in-place).
        Skips if partial_hash already set.
        """
        if meta.partial_hash:
            return meta
        try:
            meta.partial_hash = self._partial.compute(meta.path)
            logger.debug("Partial hash OK: %s → %s", meta.filename, meta.partial_hash[:8])
        except PermissionError:
            logger.warning("Permission denied computing partial hash: %s", meta.path)
        except FileNotFoundError:
            logger.warning("File disappeared before partial hash: %s", meta.path)
        except OSError as exc:
            logger.error("OS error during partial hash %s: %s", meta.path, exc)
        return meta

    def compute_full(self, meta: FileMetadata) -> FileMetadata:
        """
        Compute and store the full SHA-256 hash on the metadata object.

        Returns the same metadata object (mutated in-place).
        Skips if full_hash already set.
        """
        if meta.full_hash:
            return meta
        try:
            meta.full_hash = self._full.compute(meta.path)
            logger.debug("Full hash OK: %s → %s", meta.filename, meta.full_hash[:8])
        except PermissionError:
            logger.warning("Permission denied computing full hash: %s", meta.path)
        except FileNotFoundError:
            logger.warning("File disappeared before full hash: %s", meta.path)
        except OSError as exc:
            logger.error("OS error during full hash %s: %s", meta.path, exc)
        return meta
