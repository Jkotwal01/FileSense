"""PersistentIndex — SQLite-backed file index for incremental scanning.

Stores file metadata and computed hashes so repeated scans can reuse
previously calculated hashes for unchanged files.

Database location: ~/.filesense/filesense.db
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from filesense.metadata import FileMetadata

logger = logging.getLogger(__name__)

# Default database directory and file
DB_DIR = Path.home() / ".filesense"
DB_PATH = DB_DIR / "filesense.db"

_CREATE_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT UNIQUE NOT NULL,
    size         INTEGER,
    mtime        REAL,
    inode        TEXT,
    extension    TEXT,
    partial_hash TEXT,
    full_hash    TEXT,
    last_scanned DATETIME,
    status       TEXT DEFAULT 'active'
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_full_hash ON files(full_hash);
"""


class PersistentIndex:
    """
    SQLite-backed persistent index for file metadata and hashes.

    Responsibilities:
      - Initialize and migrate the database schema
      - Upsert file records after scanning / hashing
      - Look up existing records by path
      - Determine whether a file has changed since last scan
      - Provide all indexed records for reporting
    """

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._ensure_db_dir()
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")  # better concurrency
        self._init_schema()
        logger.info("PersistentIndex opened: %s", self.db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_db_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_CREATE_FILES_TABLE + _CREATE_INDEX)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, meta: FileMetadata) -> None:
        """Insert or update a file record in the index."""
        now = datetime.utcnow().isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO files
                    (path, size, mtime, inode, extension, partial_hash,
                     full_hash, last_scanned, status)
                VALUES
                    (:path, :size, :mtime, :inode, :ext, :ph, :fh, :ts, 'active')
                ON CONFLICT(path) DO UPDATE SET
                    size         = excluded.size,
                    mtime        = excluded.mtime,
                    inode        = excluded.inode,
                    extension    = excluded.extension,
                    partial_hash = excluded.partial_hash,
                    full_hash    = excluded.full_hash,
                    last_scanned = excluded.last_scanned,
                    status       = 'active'
                """,
                {
                    "path": meta.path,
                    "size": meta.size,
                    "mtime": meta.mtime,
                    "inode": meta.inode,
                    "ext": meta.extension,
                    "ph": meta.partial_hash,
                    "fh": meta.full_hash,
                    "ts": now,
                },
            )

    def get(self, path: str) -> Optional[FileMetadata]:
        """Return the indexed FileMetadata for a path, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_meta(row)

    def is_changed(self, meta: FileMetadata) -> bool:
        """
        Return True if the file has changed since it was last indexed,
        or if it has never been indexed.
        """
        indexed = self.get(meta.path)
        if indexed is None:
            return True  # never seen before
        return meta.is_changed(indexed)

    def load_hashes(self, meta: FileMetadata) -> FileMetadata:
        """
        If the file is unchanged, copy hashes from the index into the metadata object.
        Returns the (possibly mutated) metadata object.
        """
        if not self.is_changed(meta):
            indexed = self.get(meta.path)
            if indexed:
                meta.partial_hash = indexed.partial_hash
                meta.full_hash = indexed.full_hash
                logger.debug("Cache hit (incremental): %s", meta.filename)
        return meta

    def mark_deleted(self, path: str) -> None:
        """Mark a previously indexed file as deleted."""
        with self._conn:
            self._conn.execute(
                "UPDATE files SET status = 'deleted' WHERE path = ?", (path,)
            )

    def get_all_active(self) -> list[FileMetadata]:
        """Return all active (non-deleted) file records from the index."""
        rows = self._conn.execute(
            "SELECT * FROM files WHERE status = 'active'"
        ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    def stats(self) -> dict:
        """Return summary statistics about the current index."""
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(full_hash) AS hashed,
                SUM(size) AS total_bytes
            FROM files
            WHERE status = 'active'
            """
        ).fetchone()
        return {
            "total_files": row["total"],
            "hashed_files": row["hashed"],
            "total_bytes": row["total_bytes"] or 0,
            "db_path": str(self.db_path),
        }

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> FileMetadata:
        """Convert a DB row into a FileMetadata object."""
        path = Path(row["path"])
        return FileMetadata(
            path=row["path"],
            filename=path.name,
            extension=row["extension"] or "",
            size=row["size"] or 0,
            mtime=row["mtime"] or 0.0,
            ctime=0.0,  # not stored in DB; not needed for comparison
            inode=row["inode"] or "",
            permissions="",
            partial_hash=row["partial_hash"],
            full_hash=row["full_hash"],
            last_scanned=(
                datetime.fromisoformat(row["last_scanned"])
                if row["last_scanned"]
                else None
            ),
            status=row["status"] or "active",
        )
