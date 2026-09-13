"""Unit tests for StorageAnalyzer."""

import pytest

from filesense.duplicate import DuplicateGroup
from filesense.metadata import FileMetadata
from filesense.storage import StorageAnalyzer, _human_size


def _meta(path: str, size: int, ext: str = ".txt", category: str = "Documents") -> FileMetadata:
    return FileMetadata(
        path=path, filename=path.split("/")[-1], extension=ext,
        size=size, mtime=1.0, ctime=1.0, inode="1", permissions="0o644",
        category=category
    )


class TestHumanSize:
    def test_bytes(self):
        assert "B" in _human_size(500)

    def test_kilobytes(self):
        assert "KB" in _human_size(2000)

    def test_megabytes(self):
        assert "MB" in _human_size(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in _human_size(2 * 1024 ** 3)


class TestStorageAnalyzer:
    def setup_method(self):
        self.analyzer = StorageAnalyzer()
        self.files = [
            _meta("/a/doc.pdf", 1000, ".pdf", "Documents"),
            _meta("/a/img.jpg", 2000, ".jpg", "Images"),
            _meta("/b/vid.mp4", 5000, ".mp4", "Videos"),
            _meta("/b/code.py", 500,  ".py",  "Code"),
        ]

    def test_total_usage(self):
        usage = self.analyzer.total_usage(self.files)
        assert usage["file_count"] == 4
        assert usage["total_bytes"] == 8500

    def test_total_usage_empty(self):
        usage = self.analyzer.total_usage([])
        assert usage["file_count"] == 0
        assert usage["total_bytes"] == 0

    def test_by_extension_sorted(self):
        exts = self.analyzer.by_extension(self.files)
        # .mp4 is the largest (5000 bytes), should be first
        assert exts[0]["extension"] == ".mp4"
        assert exts[0]["bytes"] == 5000

    def test_by_extension_counts(self):
        exts = self.analyzer.by_extension(self.files)
        ext_map = {e["extension"]: e for e in exts}
        assert ext_map[".pdf"]["count"] == 1
        assert ext_map[".jpg"]["count"] == 1

    def test_by_category_sorted(self):
        cats = self.analyzer.by_category(self.files)
        assert cats[0]["category"] == "Videos"  # largest

    def test_largest_files_top_n(self):
        largest = self.analyzer.largest_files(self.files, top=2)
        assert len(largest) == 2
        assert largest[0].size >= largest[1].size

    def test_largest_files_correct_order(self):
        largest = self.analyzer.largest_files(self.files, top=4)
        sizes = [f.size for f in largest]
        assert sizes == sorted(sizes, reverse=True)

    def test_duplicate_summary(self):
        g = DuplicateGroup(
            full_hash="abc",
            files=[
                _meta("/a/x.txt", 1000),
                _meta("/b/x.txt", 1000),
                _meta("/c/x.txt", 1000),
            ]
        )
        summary = self.analyzer.duplicate_summary([g])
        assert summary["group_count"] == 1
        assert summary["duplicate_file_count"] == 3
        assert summary["wasted_bytes"] == 2000  # (3-1) * 1000

    def test_duplicate_summary_empty(self):
        summary = self.analyzer.duplicate_summary([])
        assert summary["group_count"] == 0
        assert summary["wasted_bytes"] == 0
