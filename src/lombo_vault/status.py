"""The status document: a small versioned JSON file a monitor can read (docs/contracts.md).

`backup` rewrites its half and `drill` its half; each keeps the other half as it was. Written to a temporary name and
renamed, so a reader sees the old file or the new one, never half of one. Pure except `record()`.
"""

import contextlib
import json
import os
from pathlib import Path
from typing import Any

VERSION = 1
MAX_TEXT = 300  # characters kept of a message


def clean(text: str, limit: int = MAX_TEXT) -> str:
    """Control characters (and other unprintable ones) become '?', and the text is cut to `limit`."""
    return "".join(c if c.isprintable() else "?" for c in text)[:limit]


def load(path: Path) -> dict[str, Any]:
    """The existing document, or an empty one when the file is missing, damaged or of another version."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) and doc.get("version") == VERSION else {}


def build(old: dict[str, Any], section: str, entry: dict[str, Any], ok: bool, now: float) -> dict[str, Any]:
    """The new document: `section` ("backup" or "drill") replaced by `entry`, the other half kept as it was.
    `last_success` is the time of the newest success and survives later failures."""
    previous = old.get(section)
    last = previous.get("last_success") if isinstance(previous, dict) else None
    entry = {
        **entry,
        "time": int(now),
        "last_success": int(now) if ok else (last if isinstance(last, int) else None),
        "message": clean(str(entry["message"])),
    }
    other = "drill" if section == "backup" else "backup"
    kept = old.get(other)
    return {
        "version": VERSION,
        "updated": int(now),
        "backup": entry if section == "backup" else (kept if isinstance(kept, dict) else None),
        "drill": entry if section == "drill" else (kept if isinstance(kept, dict) else None),
    }


def write(path: Path, doc: dict[str, Any]) -> None:
    """Atomic, mode 0644 (it holds counts and messages, no secrets, and a monitor running as another user reads it).
    Raises OSError."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o644)
            f.write(json.dumps(doc))
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def record(path: Path, section: str, entry: dict[str, Any], ok: bool, now: float) -> None:
    write(path, build(load(path), section, entry, ok, now))
