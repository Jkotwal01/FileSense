"""Unit tests for DuplicateDetector and DuplicateGroup."""

import os
import tempfile
from pathlib import Path

import pytest

from filesense.duplicate import DuplicateDetector, DuplicateGroup
from filesense.metadata import FileMetadata


def _tmp_dir_with_files(files: dict[str, bytes]) -> Path:
    """Create a temp dir with the given filename->content mapping."""
    tmpdir = Path(tempfile.mkdtemp())
    for name, content in files.items():
        p = tmpdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return tmpdir


def _make_meta(p: Path) -> FileMetadata:
    st = p.stat()
    return FileMetadata(
        path=str(p), filename=p.name, extension=p.suffix,
        size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
        inode=str(st.st_ino), permissions="0o644"
    )


class TestDuplicateDetector:
    def test_finds_exact_duplicates(self):
        """AC-01: Two identical files must be detected as duplicates."""
        content = b"Identical content for duplicate test" * 2000
        tmpdir = _tmp_dir_with_files({
            "a.txt": content,
            "b.txt": content,
            "c.txt": b"Different content entirely" * 2000,
        })
        files = [_make_meta(p) for p in tmpdir.iterdir() if p.is_file()]
        detector = DuplicateDetector(workers=1)
        groups = detector.detect(files)

        assert len(groups) == 1
        assert groups[0].file_count == 2
        filenames = {f.filename for f in groups[0].files}
        assert filenames == {"a.txt", "b.txt"}

    def test_different_sizes_not_duplicates(self):
        """AC-02: Files with different sizes must not be hashed together."""
        tmpdir = _tmp_dir_with_files({
            "small.txt": b"A" * 100,
            "large.txt": b"A" * 200,
        })
        files = [_make_meta(p) for p in tmpdir.iterdir() if p.is_file()]
        detector = DuplicateDetector(workers=1)
        groups = detector.detect(files)
        assert len(groups) == 0

    def test_same_name_different_content_not_duplicate(self):
        """Business Rule BR-01: filename is NOT the duplicate criterion."""
        tmpdir = _tmp_dir_with_files({
            "report.txt": b"Version A content",
            "report_copy.txt": b"Version B content completely different",
        })
        files = [_make_meta(p) for p in tmpdir.iterdir() if p.is_file()]
        detector = DuplicateDetector(workers=1)
        groups = detector.detect(files)
        assert len(groups) == 0

    def test_different_name_same_content_is_duplicate(self):
        """Business Rule BR-02: different filenames, same content = duplicate."""
        content = b"Same bytes in both files" * 3000
        tmpdir = _tmp_dir_with_files({
            "original.pdf": content,
            "backup_original.pdf": content,
            "final_v2.pdf": content,
        })
        files = [_make_meta(p) for p in tmpdir.iterdir() if p.is_file()]
        detector = DuplicateDetector(workers=2)
        groups = detector.detect(files)
        assert len(groups) == 1
        assert groups[0].file_count == 3

    def test_no_files_returns_empty(self):
        groups = DuplicateDetector().detect([])
        assert groups == []

    def test_single_file_returns_empty(self):
        tmpdir = _tmp_dir_with_files({"only.txt": b"solo"})
        files = [_make_meta(p) for p in tmpdir.iterdir() if p.is_file()]
        groups = DuplicateDetector().detect(files)
        assert groups == []

    def test_cached_hashes_reused(self):
        """Files with full_hash pre-set should not be rehashed."""
        meta1 = FileMetadata(
            path="/fake/a.txt", filename="a.txt", extension=".txt",
            size=100, mtime=1.0, ctime=1.0, inode="1", permissions="0o644",
            partial_hash="aabbcc", full_hash="deadbeef1234"
        )
        meta2 = FileMetadata(
            path="/fake/b.txt", filename="b.txt", extension=".txt",
            size=100, mtime=1.0, ctime=1.0, inode="2", permissions="0o644",
            partial_hash="aabbcc", full_hash="deadbeef1234"
        )
        groups = DuplicateDetector().detect([meta1, meta2])
        assert len(groups) == 1


class TestDuplicateGroup:
    def _group(self, files: list[FileMetadata]) -> DuplicateGroup:
        return DuplicateGroup(full_hash="abc", files=files)

    def _meta(self, path: str, size: int = 1024) -> FileMetadata:
        return FileMetadata(
            path=path, filename=Path(path).name, extension=".txt",
            size=size, mtime=1.0, ctime=1.0, inode="1", permissions="0o644"
        )

    def test_file_count(self):
        g = self._group([self._meta("/a/x.txt"), self._meta("/b/y.txt")])
        assert g.file_count == 2

    def test_wasted_size(self):
        g = self._group([self._meta("/a/x.txt", 1000), self._meta("/b/y.txt", 1000)])
        assert g.wasted_size == 1000  # (2-1) * 1000

    def test_original_candidate_shortest_path(self):
        g = self._group([
            self._meta("/very/long/path/to/file.txt"),
            self._meta("/a/b.txt"),
        ])
        assert g.original_candidate.path == "/a/b.txt"

    def test_duplicates_excludes_original(self):
        orig = self._meta("/a/b.txt")
        dup = self._meta("/very/long/path/to/file.txt")
        g = self._group([orig, dup])
        dups = g.duplicates
        assert len(dups) == 1
        assert dups[0].path == "/very/long/path/to/file.txt"
