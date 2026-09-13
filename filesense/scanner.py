"""FileScanner — recursively traverses directories and collects file metadata."""

import logging
import os
import stat
from pathlib import Path
from typing import Iterator

from filesense.metadata import FileMetadata

logger = logging.getLogger(__name__)


class ScanResult:
    """Holds the aggregate result of a directory scan."""

    def __init__(self) -> None:
        self.files: list[FileMetadata] = []
        self.directory_count: int = 0
        self.error_count: int = 0
        self.errors: list[str] = []

    def add_file(self, meta: FileMetadata) -> None:
        self.files.append(meta)

    def add_error(self, message: str) -> None:
        self.error_count += 1
        self.errors.append(message)
        logger.warning(message)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


class FileScanner:
    """
    Recursively scans a directory tree and collects FileMetadata for each file.

    Responsibilities:
      - Traverse directories (optionally following symlinks)
      - Collect OS-level metadata for each file
      - Skip inaccessible files gracefully (log warning, continue)
      - Return a ScanResult aggregate
    """

    def __init__(self, follow_symlinks: bool = False) -> None:
        self.follow_symlinks = follow_symlinks

    def scan(self, root: str | Path) -> ScanResult:
        """
        Scan the given root directory recursively.

        Args:
            root: Path to the directory to scan.

        Returns:
            ScanResult containing all discovered FileMetadata objects.
        """
        root = Path(root).resolve()
        result = ScanResult()

        if not root.exists():
            raise FileNotFoundError(f"Directory not found: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {root}")

        logger.info("Scan started: %s", root)

        for entry in self._walk(root, result):
            try:
                meta = self._collect_metadata(entry)
                result.add_file(meta)
            except PermissionError:
                result.add_error(f"Permission denied: {entry}")
            except FileNotFoundError:
                result.add_error(f"File disappeared during scan: {entry}")
            except OSError as exc:
                result.add_error(f"OS error reading {entry}: {exc}")

        logger.info(
            "Scan completed: %d files, %d dirs, %d errors",
            result.file_count,
            result.directory_count,
            result.error_count,
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _walk(self, root: Path, result: ScanResult) -> Iterator[Path]:
        """Yield file paths from a recursive directory walk."""
        try:
            entries = list(root.iterdir())
        except PermissionError:
            result.add_error(f"Permission denied (directory): {root}")
            return
        except OSError as exc:
            result.add_error(f"Cannot read directory {root}: {exc}")
            return

        for entry in entries:
            try:
                # Handle symlinks explicitly before calling is_dir/is_file
                if entry.is_symlink():
                    if not self.follow_symlinks:
                        logger.debug("Skipping symlink: %s", entry)
                        continue
                    # follow_symlinks=True: resolve and check the target
                    resolved = entry.resolve()
                    if resolved.is_dir():
                        result.directory_count += 1
                        yield from self._walk(entry, result)
                    elif resolved.is_file():
                        yield entry
                elif entry.is_dir():
                    result.directory_count += 1
                    yield from self._walk(entry, result)
                elif entry.is_file():
                    yield entry
            except PermissionError:
                result.add_error(f"Permission denied: {entry}")
            except OSError as exc:
                result.add_error(f"OS error on {entry}: {exc}")

    def _collect_metadata(self, path: Path) -> FileMetadata:
        """Stat a file and build its FileMetadata."""
        st = path.stat()

        # Windows does not expose a real inode; use file index if available
        inode = str(st.st_ino) if st.st_ino else str(hash(str(path)))
        permissions = oct(stat.S_IMODE(st.st_mode))

        return FileMetadata(
            path=str(path),
            filename=path.name,
            extension=path.suffix.lower(),
            size=st.st_size,
            mtime=st.st_mtime,
            ctime=st.st_ctime,
            inode=inode,
            permissions=permissions,
        )
