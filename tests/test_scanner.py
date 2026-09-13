"""Unit tests for FileScanner and FileMetadata."""

import os
import tempfile
from pathlib import Path

import pytest

from filesense.metadata import FileMetadata, classify_extension
from filesense.scanner import FileScanner, ScanResult


# ---------------------------------------------------------------------------
# classify_extension
# ---------------------------------------------------------------------------

class TestClassifyExtension:
    def test_known_image(self):
        assert classify_extension(".jpg") == "Images"

    def test_known_video(self):
        assert classify_extension(".mp4") == "Videos"

    def test_known_code(self):
        assert classify_extension(".py") == "Code"

    def test_unknown_returns_other(self):
        assert classify_extension(".xyz123") == "Other"

    def test_case_insensitive(self):
        assert classify_extension(".JPG") == "Images"
        assert classify_extension(".PDF") == "Documents"


# ---------------------------------------------------------------------------
# FileMetadata
# ---------------------------------------------------------------------------

class TestFileMetadata:
    def _make_meta(self, size=1000, mtime=1000.0, path="/a/test.txt"):
        return FileMetadata(
            path=path, filename="test.txt", extension=".txt",
            size=size, mtime=mtime, ctime=mtime,
            inode="1", permissions="0o644"
        )

    def test_category_auto_classified(self):
        meta = self._make_meta()
        assert meta.category == "Documents"

    def test_human_size_bytes(self):
        meta = self._make_meta(size=500)
        assert "B" in meta.human_size()

    def test_human_size_mb(self):
        meta = self._make_meta(size=5 * 1024 * 1024)
        assert "MB" in meta.human_size()

    def test_is_changed_same(self):
        a = self._make_meta()
        b = self._make_meta()
        assert a.is_changed(b) is False

    def test_is_changed_size(self):
        a = self._make_meta(size=100)
        b = self._make_meta(size=200)
        assert a.is_changed(b) is True

    def test_is_changed_mtime(self):
        a = self._make_meta(mtime=1000.0)
        b = self._make_meta(mtime=2000.0)
        assert a.is_changed(b) is True


# ---------------------------------------------------------------------------
# FileScanner
# ---------------------------------------------------------------------------

class TestFileScanner:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.scanner = FileScanner()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name: str, content: bytes = b"test") -> Path:
        p = self.tmpdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_scan_finds_files(self):
        self._write("a.txt")
        self._write("b.txt")
        result = self.scanner.scan(self.tmpdir)
        assert result.file_count == 2

    def test_scan_recursive(self):
        self._write("sub/a.txt")
        self._write("sub/nested/b.txt")
        result = self.scanner.scan(self.tmpdir)
        assert result.file_count == 2
        assert result.directory_count >= 2

    def test_scan_empty_dir(self):
        result = self.scanner.scan(self.tmpdir)
        assert result.file_count == 0
        assert result.error_count == 0

    def test_scan_metadata_correct(self):
        p = self._write("file.py", b"hello")
        result = self.scanner.scan(self.tmpdir)
        assert result.file_count == 1
        meta = result.files[0]
        assert meta.filename == "file.py"
        assert meta.extension == ".py"
        assert meta.size == 5
        assert meta.category == "Code"

    def test_scan_invalid_path_raises(self):
        with pytest.raises(FileNotFoundError):
            self.scanner.scan("/nonexistent/path/that/does/not/exist")

    def test_scan_file_path_raises(self):
        p = self._write("file.txt")
        with pytest.raises(NotADirectoryError):
            self.scanner.scan(str(p))

    def test_scan_result_total_size(self):
        self._write("a.txt", b"A" * 100)
        self._write("b.txt", b"B" * 200)
        result = self.scanner.scan(self.tmpdir)
        assert result.total_size == 300
