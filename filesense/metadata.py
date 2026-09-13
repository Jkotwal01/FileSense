"""FileMetadata — dataclass representing a single file's metadata."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# File category classification map
EXTENSION_CATEGORIES: dict[str, str] = {
    # Images
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images",
    ".gif": "Images", ".bmp": "Images", ".webp": "Images",
    ".tiff": "Images", ".svg": "Images", ".ico": "Images",
    # Videos
    ".mp4": "Videos", ".mkv": "Videos", ".avi": "Videos",
    ".mov": "Videos", ".wmv": "Videos", ".flv": "Videos",
    ".webm": "Videos", ".m4v": "Videos",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio",
    ".aac": "Audio", ".ogg": "Audio", ".m4a": "Audio",
    # Documents
    ".pdf": "Documents", ".docx": "Documents", ".doc": "Documents",
    ".txt": "Documents", ".xlsx": "Documents", ".xls": "Documents",
    ".pptx": "Documents", ".ppt": "Documents", ".odt": "Documents",
    ".csv": "Documents", ".md": "Documents",
    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives", ".bz2": "Archives",
    ".xz": "Archives", ".iso": "Archives",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".java": "Code",
    ".cpp": "Code", ".c": "Code", ".h": "Code", ".cs": "Code",
    ".go": "Code", ".rs": "Code", ".rb": "Code", ".php": "Code",
    ".html": "Code", ".css": "Code", ".json": "Code", ".xml": "Code",
    ".yaml": "Code", ".yml": "Code", ".sh": "Code", ".bat": "Code",
    # Executables
    ".exe": "Executables", ".dll": "Executables", ".so": "Executables",
    ".msi": "Executables", ".apk": "Executables",
}


def classify_extension(extension: str) -> str:
    """Return a human-readable category for the given file extension."""
    return EXTENSION_CATEGORIES.get(extension.lower(), "Other")


@dataclass
class FileMetadata:
    """Holds all metadata for a single discovered file."""

    path: str
    filename: str
    extension: str
    size: int                       # bytes
    mtime: float                    # last modified timestamp (epoch)
    ctime: float                    # creation/change timestamp (epoch)
    inode: str                      # inode or file-id for identity tracking
    permissions: str                # octal permission string
    category: str = "Other"
    partial_hash: Optional[str] = None
    full_hash: Optional[str] = None
    last_scanned: Optional[datetime] = None
    status: str = "active"          # active | deleted

    def __post_init__(self) -> None:
        if not self.category or self.category == "Other":
            self.category = classify_extension(self.extension)

    def is_changed(self, other: "FileMetadata") -> bool:
        """Return True if size or mtime differs from another (indexed) record."""
        return self.size != other.size or abs(self.mtime - other.mtime) > 0.001

    def human_size(self) -> str:
        """Return a human-readable file size string."""
        size = self.size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
