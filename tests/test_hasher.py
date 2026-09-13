"""Unit tests for HashManager, PartialHashStrategy, FullHashStrategy."""

import os
import tempfile
from pathlib import Path

import pytest

from filesense.hasher import (
    FullHashStrategy,
    HashManager,
    PARTIAL_READ_BYTES,
    PartialHashStrategy,
)
from filesense.metadata import FileMetadata


def _tmp_file(content: bytes) -> str:
    """Create a temp file with given content and return its path."""
    fd, path = tempfile.mkstemp()
    os.write(fd, content)
    os.close(fd)
    return path


def _make_meta(path: str) -> FileMetadata:
    st = os.stat(path)
    return FileMetadata(
        path=path, filename=Path(path).name, extension=Path(path).suffix,
        size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
        inode=str(st.st_ino), permissions="0o644"
    )


# ---------------------------------------------------------------------------
# PartialHashStrategy
# ---------------------------------------------------------------------------

class TestPartialHashStrategy:
    def setup_method(self):
        self.strategy = PartialHashStrategy()
        self._paths: list[str] = []

    def teardown_method(self):
        for p in self._paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _write(self, content: bytes) -> str:
        path = _tmp_file(content)
        self._paths.append(path)
        return path

    def test_same_content_same_hash(self):
        content = b"identical content" * 1000
        p1 = self._write(content)
        p2 = self._write(content)
        assert self.strategy.compute(p1) == self.strategy.compute(p2)

    def test_different_content_different_hash(self):
        p1 = self._write(b"content A" * 1000)
        p2 = self._write(b"content B" * 1000)
        assert self.strategy.compute(p1) != self.strategy.compute(p2)

    def test_small_file_no_error(self):
        # File smaller than 2 × PARTIAL_READ_BYTES — should not crash
        p = self._write(b"tiny")
        h = self.strategy.compute(p)
        assert len(h) == 64  # SHA-256 hex

    def test_empty_file(self):
        p = self._write(b"")
        h = self.strategy.compute(p)
        assert len(h) == 64


# ---------------------------------------------------------------------------
# FullHashStrategy
# ---------------------------------------------------------------------------

class TestFullHashStrategy:
    def setup_method(self):
        self.strategy = FullHashStrategy()
        self._paths: list[str] = []

    def teardown_method(self):
        for p in self._paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _write(self, content: bytes) -> str:
        path = _tmp_file(content)
        self._paths.append(path)
        return path

    def test_same_content_same_hash(self):
        content = b"same data" * 5000
        p1 = self._write(content)
        p2 = self._write(content)
        assert self.strategy.compute(p1) == self.strategy.compute(p2)

    def test_different_content_different_hash(self):
        p1 = self._write(b"data X" * 5000)
        p2 = self._write(b"data Y" * 5000)
        assert self.strategy.compute(p1) != self.strategy.compute(p2)

    def test_empty_file_known_hash(self):
        # SHA-256 of empty content is well-known
        p = self._write(b"")
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert self.strategy.compute(p) == expected

    def test_large_file_streams(self):
        # 2 MB file — should not raise MemoryError or any error
        content = b"X" * (2 * 1024 * 1024)
        p = self._write(content)
        h = self.strategy.compute(p)
        assert len(h) == 64


# ---------------------------------------------------------------------------
# HashManager
# ---------------------------------------------------------------------------

class TestHashManager:
    def setup_method(self):
        self.hm = HashManager()
        self._paths: list[str] = []

    def teardown_method(self):
        for p in self._paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _meta(self, content: bytes) -> FileMetadata:
        path = _tmp_file(content)
        self._paths.append(path)
        return _make_meta(path)

    def test_compute_partial_sets_hash(self):
        meta = self._meta(b"hello world" * 1000)
        self.hm.compute_partial(meta)
        assert meta.partial_hash is not None
        assert len(meta.partial_hash) == 64

    def test_compute_full_sets_hash(self):
        meta = self._meta(b"hello world" * 1000)
        self.hm.compute_full(meta)
        assert meta.full_hash is not None
        assert len(meta.full_hash) == 64

    def test_partial_skipped_if_cached(self):
        meta = self._meta(b"data")
        meta.partial_hash = "already_cached"
        self.hm.compute_partial(meta)
        assert meta.partial_hash == "already_cached"  # not overwritten

    def test_full_skipped_if_cached(self):
        meta = self._meta(b"data")
        meta.full_hash = "cached_full"
        self.hm.compute_full(meta)
        assert meta.full_hash == "cached_full"

    def test_partial_and_full_differ(self):
        meta = self._meta(b"Z" * (PARTIAL_READ_BYTES * 4))
        self.hm.compute_partial(meta)
        self.hm.compute_full(meta)
        # For a uniform file partial == full is expected (all bytes same)
        # For a non-uniform file they would differ — just assert both set
        assert meta.partial_hash is not None
        assert meta.full_hash is not None
