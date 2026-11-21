"""The decisions. Pure: no disk, no network, no clock (the time is always passed in), so tests need no fakes.

Naming of backup files, which files belong to the tool, what retention may delete, how the manifest
is built and compared, and what a drill result means. cli.py and store.py do the talking to the disk.
"""

import fnmatch
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

PREFIX = "lombovault_"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
ARCHIVE_SUFFIX = ".tar.gz.age"
MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_VERSION = 1
AGE_HEADER = b"age-encryption.org/v1"
STALE_TEMP_SECONDS = 24 * 3600

# The ONLY names retention is allowed to touch. Anything else in the backup folder is never deleted.
_STAMP = r"([0-9]{8}T[0-9]{6}Z)"
_FINAL_RE = re.compile(rf"{PREFIX}{_STAMP}(\.tar\.gz\.age|\.manifest\.json)", re.ASCII)
_TEMP_RE = re.compile(rf"\.{PREFIX}{_STAMP}(\.tar\.gz\.age|\.manifest\.json)\.tmp", re.ASCII)
MAX_FUTURE_SECONDS = 24 * 3600  # a stamp further ahead than this means the clock was wrong
_HEX64 = re.compile(r"[0-9a-f]{64}")


def stamp_of(now: datetime) -> str:
    return now.astimezone(UTC).strftime(STAMP_FORMAT)


def archive_name(stamp: str) -> str:
    return f"{PREFIX}{stamp}{ARCHIVE_SUFFIX}"


def manifest_name(stamp: str) -> str:
    return f"{PREFIX}{stamp}{MANIFEST_SUFFIX}"


def temp_name(final_name: str) -> str:
    return f".{final_name}.tmp"


def parse_name(name: str) -> tuple[str, str] | None:
    """('20261121T043000Z', 'archive'|'manifest') for the tool's own final files, else None."""
    m = _FINAL_RE.fullmatch(name)
    if not m or not _is_date(m.group(1)):
        return None
    return m.group(1), "archive" if m.group(2) == ARCHIVE_SUFFIX else "manifest"


def _is_date(stamp: str) -> bool:
    """True when the digits are a real date and time (99999999T999999Z is not)."""
    try:
        datetime.strptime(stamp, STAMP_FORMAT)
    except ValueError:
        return False
    return True


def is_own_file(name: str) -> bool:
    """True for the tool's own final or temporary file names, and nothing else."""
    if _FINAL_RE.fullmatch(name):
        return parse_name(name) is not None
    m = _TEMP_RE.fullmatch(name)
    return bool(m and _is_date(m.group(1)))


def stamp_to_datetime(stamp: str) -> datetime:
    return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)


def age_days(stamp: str, now: datetime) -> float:
    return (now - stamp_to_datetime(stamp)).total_seconds() / 86400


def runs(names: list[str]) -> dict[str, set[str]]:
    """Group the tool's own files by run: {stamp: {'archive', 'manifest'}}."""
    found: dict[str, set[str]] = {}
    for name in names:
        parsed = parse_name(name)
        if parsed:
            found.setdefault(parsed[0], set()).add(parsed[1])
    return found


def complete_stamps(names: list[str]) -> list[str]:
    """Stamps that have both an archive and a manifest, newest first."""
    return sorted((s for s, kinds in runs(names).items() if kinds == {"archive", "manifest"}), reverse=True)


def plan_prune(files: dict[str, float], keep: int, now_epoch: float) -> list[str]:
    """Names retention may delete. `files` maps a file name in the backup folder to its modification time.

    Rules, all of them leaning towards keeping:
    - only names matching the tool's own pattern are ever returned;
    - the newest `keep` COMPLETE runs (archive and manifest) are kept; with fewer than `keep`, nothing is deleted;
    - older runs go, including half-written ones older than the oldest kept run;
    - leftover temp files from a crashed backup go only when over a day old.
    """
    doomed: list[str] = []
    # A stamp from the future (wrong clock) is never counted as a kept run and never ordered against real ones.
    latest = datetime.fromtimestamp(now_epoch + MAX_FUTURE_SECONDS, UTC).strftime(STAMP_FORMAT)
    complete = [s for s in complete_stamps(list(files)) if s <= latest]
    if keep >= 1 and len(complete) >= keep:
        oldest_kept = complete[keep - 1]
        for name in sorted(files):
            parsed = parse_name(name)
            if parsed and parsed[0] < oldest_kept:
                doomed.append(name)
    for name in sorted(files):
        if _TEMP_RE.fullmatch(name) and now_epoch - files[name] > STALE_TEMP_SECONDS:
            doomed.append(name)
    return doomed


def overlaps(a: PurePath, b: PurePath) -> bool:
    """True when one path is the same as, or inside, the other (paths must already be resolved)."""
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def conflicts(backup_dir: PurePath, sources: Sequence[PurePath]) -> list[str]:
    """Why this backup folder cannot be used with these sources (empty list means fine)."""
    problems = []
    for src in sources:
        if backup_dir.is_relative_to(src):
            problems.append(
                f"the backup folder {backup_dir} is inside the source {src} (the backups would back themselves up)"
            )
        elif src.is_relative_to(backup_dir):
            problems.append(f"the source {src} is inside the backup folder {backup_dir}")
    return problems


def is_excluded(path: str, patterns: tuple[str, ...], source: PurePath | None = None) -> bool:
    """A glob matches the whole path or any folder or file name in it (fnmatch rules).

    `source`, when given, is the source folder the path belongs to: only the part below it is matched by name,
    so a folder ABOVE the source (a source at /x/cache/app with EXCLUDE=cache) never excludes the whole source.
    """
    below = PurePath(path)
    if source is not None and below.is_relative_to(source):
        below = below.relative_to(source)
    parts = below.parts
    return any(fnmatch.fnmatchcase(path, p) or any(fnmatch.fnmatchcase(part, p) for part in parts) for p in patterns)


def member_name(path: str) -> str:
    """Name inside the archive: the absolute path without its leading slash."""
    return path.lstrip("/")


@dataclass(frozen=True)
class Manifest:
    created: str
    archive: str
    archive_sha256: str
    archive_size: int
    files: dict[str, str]  # archive member name -> sha256 of its content
    total_bytes: int


class ManifestError(Exception):
    """The manifest file is not one this tool wrote, or is damaged."""


def build_manifest(
    stamp: str, archive_sha256: str, archive_size: int, files: dict[str, tuple[str, int]], sources: list[str]
) -> str:
    """JSON text. `files` maps member name -> (sha256, size)."""
    doc = {
        "version": MANIFEST_VERSION,
        "created": stamp,
        "archive": archive_name(stamp),
        "archive_sha256": archive_sha256,
        "archive_size": archive_size,
        "sources": sources,
        "files": {name: {"sha256": h, "size": size} for name, (h, size) in sorted(files.items())},
    }
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"


def parse_manifest(text: str, expected_stamp: str) -> Manifest:
    try:
        doc: Any = json.loads(text)
    except ValueError as e:
        raise ManifestError(f"the manifest is not valid JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("version") != MANIFEST_VERSION:
        raise ManifestError("the manifest has an unknown version")
    if doc.get("created") != expected_stamp or doc.get("archive") != archive_name(expected_stamp):
        raise ManifestError("the manifest does not belong to this archive (names differ)")
    sha, size, raw = doc.get("archive_sha256"), doc.get("archive_size"), doc.get("files")
    if not (isinstance(sha, str) and _HEX64.fullmatch(sha)) or not isinstance(size, int) or isinstance(size, bool):
        raise ManifestError("the manifest has a bad archive checksum or size")
    if not isinstance(raw, dict):
        raise ManifestError("the manifest has no file list")
    files: dict[str, str] = {}
    total = 0
    for name, entry in raw.items():
        ok = isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and _HEX64.fullmatch(entry["sha256"])
        fsize = entry.get("size") if isinstance(entry, dict) else None
        if not ok or not isinstance(fsize, int) or isinstance(fsize, bool) or fsize < 0:
            raise ManifestError(f"the manifest entry for {name!r} is damaged")
        if name.startswith("/") or ".." in PurePath(name).parts:
            raise ManifestError(f"the manifest names an unsafe path {name!r}")
        files[name] = entry["sha256"]
        total += fsize
    return Manifest(expected_stamp, archive_name(expected_stamp), sha, size, files, total)


@dataclass(frozen=True)
class Diff:
    missing: list[str]  # in the manifest, not found after restore
    extra: list[str]  # restored, not in the manifest
    changed: list[str]  # content differs from the checksum written at backup time

    @property
    def clean(self) -> bool:
        return not (self.missing or self.extra or self.changed)

    def summary(self, limit: int = 3) -> str:
        parts = []
        for label, items in (("missing", self.missing), ("extra", self.extra), ("changed", self.changed)):
            if items:
                shown = ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")
                parts.append(f"{len(items)} {label} ({shown})")
        return "; ".join(parts) or "identical"


def compare_manifest(expected: dict[str, str], actual: dict[str, str]) -> Diff:
    """Compare the checksums written at backup time with those of the restored files."""
    return Diff(
        missing=sorted(set(expected) - set(actual)),
        extra=sorted(set(actual) - set(expected)),
        changed=sorted(n for n in set(expected) & set(actual) if expected[n] != actual[n]),
    )


@dataclass(frozen=True)
class Verdict:
    result: str  # "ok" | "partial" | "fail"
    message: str


def space_needed(total_bytes: int) -> int:
    """Room the drill wants in the scratch folder: the content plus a margin for folders and slack."""
    return total_bytes + total_bytes // 10 + 64 * 1024 * 1024
