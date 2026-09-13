"""Logging configuration for FileSense."""

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "filesense.log"

_configured = False


def setup_logging(verbose: bool = False) -> None:
    """
    Configure root logger with:
      - File handler  → logs/filesense.log (always DEBUG level)
      - Console handler → WARNING by default, DEBUG if verbose
    """
    global _configured
    if _configured:
        return
    _configured = True

    LOG_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # File handler — rotating, max 5 MB × 3 backups
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Console handler — show WARNING+ (or DEBUG in verbose mode)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))

    root.addHandler(fh)
    root.addHandler(ch)
