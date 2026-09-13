"""FileSense entry point."""

import sys
import io

# Force UTF-8 stdout on Windows to avoid cp1252 UnicodeEncodeError with Rich
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from filesense.cli import app

if __name__ == "__main__":
    app()
