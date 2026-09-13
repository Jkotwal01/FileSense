"""StorageAnalyzer — analyzes storage consumption across multiple dimensions."""

import logging
from collections import defaultdict
from pathlib import Path

from filesense.duplicate import DuplicateGroup
from filesense.metadata import FileMetadata

logger = logging.getLogger(__name__)


def _human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


class StorageAnalyzer:
    """
    Analyzes storage usage for a collection of scanned files.

    Provides breakdowns by extension, directory, and duplicate groups.
    """

    def total_usage(self, files: list[FileMetadata]) -> dict:
        """Return overall storage statistics."""
        total_bytes = sum(f.size for f in files)
        return {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "total_human": _human_size(total_bytes),
        }

    def by_extension(self, files: list[FileMetadata]) -> list[dict]:
        """
        Return storage breakdown by file extension, sorted by total size descending.
        """
        ext_map: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
        for meta in files:
            ext = meta.extension or "(no ext)"
            ext_map[ext]["count"] += 1
            ext_map[ext]["bytes"] += meta.size

        result = [
            {
                "extension": ext,
                "count": data["count"],
                "bytes": data["bytes"],
                "human": _human_size(data["bytes"]),
            }
            for ext, data in ext_map.items()
        ]
        result.sort(key=lambda x: x["bytes"], reverse=True)
        return result

    def by_category(self, files: list[FileMetadata]) -> list[dict]:
        """Return storage breakdown by file category."""
        cat_map: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
        for meta in files:
            cat_map[meta.category]["count"] += 1
            cat_map[meta.category]["bytes"] += meta.size

        result = [
            {
                "category": cat,
                "count": data["count"],
                "bytes": data["bytes"],
                "human": _human_size(data["bytes"]),
            }
            for cat, data in cat_map.items()
        ]
        result.sort(key=lambda x: x["bytes"], reverse=True)
        return result

    def by_directory(self, files: list[FileMetadata], depth: int = 1) -> list[dict]:
        """
        Return storage breakdown by top-level directory components.

        Args:
            files: All scanned files.
            depth: Number of path components to group by (1 = immediate parent dir).
        """
        dir_map: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
        for meta in files:
            parts = Path(meta.path).parts
            # Take the directory at the given depth relative to the path
            dir_key = str(Path(*parts[: len(parts) - 1])) if len(parts) > 1 else "/"
            dir_map[dir_key]["count"] += 1
            dir_map[dir_key]["bytes"] += meta.size

        result = [
            {
                "directory": d,
                "count": data["count"],
                "bytes": data["bytes"],
                "human": _human_size(data["bytes"]),
            }
            for d, data in dir_map.items()
        ]
        result.sort(key=lambda x: x["bytes"], reverse=True)
        return result

    def largest_files(self, files: list[FileMetadata], top: int = 10) -> list[FileMetadata]:
        """Return the N largest files by size, descending."""
        return sorted(files, key=lambda f: f.size, reverse=True)[:top]

    def duplicate_summary(self, groups: list[DuplicateGroup]) -> dict:
        """Return duplicate-related storage summary."""
        total_dup_files = sum(g.file_count for g in groups)
        total_dup_bytes = sum(g.total_size for g in groups)
        total_wasted = sum(g.wasted_size for g in groups)

        return {
            "group_count": len(groups),
            "duplicate_file_count": total_dup_files,
            "duplicate_bytes": total_dup_bytes,
            "duplicate_human": _human_size(total_dup_bytes),
            "wasted_bytes": total_wasted,
            "wasted_human": _human_size(total_wasted),
        }
