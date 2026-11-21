"""Disk work: walking the sources, writing a backup atomically, reading the backup folder, deleting old files.

Decisions (what to keep, what may be deleted, whether paths clash) come from core.py.
"""

import hashlib
import os
import stat
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from . import agebin, core
from .config import Config


class BackupError(Exception):
    """The backup could not be made. Nothing was deleted and no half-written file was left."""


@dataclass(frozen=True)
class Entry:
    path: str  # absolute path on disk
    kind: str  # "dir" | "file" | "link"


@dataclass(frozen=True)
class Scan:
    entries: list[Entry]
    files: int
    bytes: int
    skipped: list[str]  # sockets, devices and excluded paths (reported, never silently dropped)


@dataclass(frozen=True)
class Written:
    stamp: str
    archive: Path
    manifest: Path
    archive_size: int
    files: int
    bytes: int


def resolved_sources(cfg: Config) -> list[Path]:
    return [p.resolve() for p in cfg.sources]


def check_layout(cfg: Config) -> list[str]:
    """Why the folders cannot be used (missing sources, backup folder inside a source...). Empty means fine."""
    problems = [f"source {p} does not exist" for p in cfg.sources if not p.exists()]
    sources = resolved_sources(cfg)
    problems += core.conflicts(cfg.backup_dir.resolve(), sources)
    for label, path in (("key file", cfg.key_file), ("scratch folder", cfg.scratch_dir)):
        if path is not None:
            inside = [s for s in sources if path.resolve().is_relative_to(s)]
            if inside:
                problems.append(f"the {label} {path} is inside the source {inside[0]} (it would be backed up)")
    return problems


def scan(cfg: Config) -> Scan:
    """List everything a backup would contain. Symlinks are stored as links, never followed."""
    entries: list[Entry] = []
    skipped: list[str] = []
    files = total = 0

    current = Path("/")

    def add(path: str) -> None:
        nonlocal files, total
        if core.is_excluded(path, cfg.exclude, current):
            skipped.append(f"{path} (excluded)")
            return
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            entries.append(Entry(path, "link"))
        elif stat.S_ISDIR(mode):
            entries.append(Entry(path, "dir"))
        elif stat.S_ISREG(mode):
            entries.append(Entry(path, "file"))
            files += 1
            total += os.lstat(path).st_size
        else:
            skipped.append(f"{path} (not a regular file)")

    def fail(e: OSError) -> None:
        raise e

    for configured in cfg.sources:
        # A source that is itself a symlink (for example /config -> /mnt/x) is followed; links inside it are not.
        src = configured.resolve()
        current = src
        try:
            add(str(src))
            if src.is_dir():
                for root, dirs, names in os.walk(src, onerror=fail):
                    dirs.sort()
                    keep_dirs = []
                    for d in dirs:
                        before = len(entries)
                        add(os.path.join(root, d))
                        if len(entries) > before and not os.path.islink(os.path.join(root, d)):
                            keep_dirs.append(d)
                    dirs[:] = keep_dirs
                    for n in sorted(names):
                        add(os.path.join(root, n))
        except OSError as e:
            raise BackupError(f"cannot read {e.filename or src}: {e.strerror or e}") from e
    return Scan(entries, files, total, skipped)


class _Hashing:
    """File wrapper for tarfile: hashes exactly the bytes that go into the archive."""

    def __init__(self, handle: IO[bytes]) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        return data


def _add_entry(tar: tarfile.TarFile, entry: Entry, hashes: dict[str, tuple[str, int]]) -> None:
    name = core.member_name(entry.path)
    st = os.lstat(entry.path)
    info = tarfile.TarInfo(name)
    info.mode, info.mtime, info.uid, info.gid = stat.S_IMODE(st.st_mode), int(st.st_mtime), st.st_uid, st.st_gid
    if entry.kind == "dir":
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    elif entry.kind == "link":
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(entry.path)
        tar.addfile(info)
    else:
        fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info.size = os.fstat(fd).st_size
            reader = _Hashing(handle)
            tar.addfile(info, reader)
        hashes[name] = (reader.digest.hexdigest(), info.size)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_backup(cfg: Config, now: datetime) -> Written:
    """Make one encrypted backup. Archive and manifest are written under temporary names and renamed only
    when both are complete, so a crash leaves at most an ignorable temp file, never a half-written backup.
    Old backups are not touched here."""
    problems = check_layout(cfg)
    if problems:
        raise BackupError("; ".join(problems))
    found = scan(cfg)
    if found.files == 0:
        raise BackupError(
            "the sources hold no files to back up (a wrong path, a mount that is not there, or EXCLUDE matching "
            "everything); nothing was written, and older backups were kept"
        )
    stamp = core.stamp_of(now)
    final_archive = cfg.backup_dir / core.archive_name(stamp)
    final_manifest = cfg.backup_dir / core.manifest_name(stamp)
    tmp_archive = cfg.backup_dir / core.temp_name(final_archive.name)
    tmp_manifest = cfg.backup_dir / core.temp_name(final_manifest.name)
    owns_temp = False
    try:
        cfg.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if final_archive.exists() or final_manifest.exists():
            raise BackupError(f"a backup named {final_archive.name} already exists (two runs in the same second?)")
        hashes: dict[str, tuple[str, int]] = {}
        # O_EXCL: a second run in the same second fails instead of writing into the first run's file.
        fd = os.open(tmp_archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owns_temp = True
        with os.fdopen(fd, "wb") as out:
            with (
                agebin.encryptor(cfg.age_bin, cfg.recipients, out) as sink,
                tarfile.open(fileobj=sink, mode="w|gz") as tar,
            ):
                for entry in found.entries:
                    _add_entry(tar, entry, hashes)
            out.flush()
            os.fsync(out.fileno())
        size = tmp_archive.stat().st_size
        with tmp_archive.open("rb") as check:
            if check.read(len(core.AGE_HEADER)) != core.AGE_HEADER:
                raise BackupError("the output does not start with an age header; is the age program a real one?")
        text = core.build_manifest(stamp, _sha256_file(tmp_archive), size, hashes, [str(p) for p in cfg.sources])
        _write_new(tmp_manifest, text.encode())
        os.replace(tmp_archive, final_archive)
        os.replace(tmp_manifest, final_manifest)
    except (OSError, agebin.AgeError, tarfile.TarError, ValueError) as e:
        raise BackupError(str(e)) from e
    finally:
        if owns_temp:  # never remove a temp file that another run created
            for leftover in (tmp_archive, tmp_manifest):
                leftover.unlink(missing_ok=True)
    return Written(stamp, final_archive, final_manifest, size, len(hashes), sum(s for _, s in hashes.values()))


def list_folder(backup_dir: Path) -> dict[str, float]:
    """Regular files directly inside the backup folder: {name: modification time}."""
    found: dict[str, float] = {}
    if not backup_dir.is_dir():
        return found
    for item in os.scandir(backup_dir):
        st = item.stat(follow_symlinks=False)
        if stat.S_ISREG(st.st_mode):
            found[item.name] = st.st_mtime
    return found


def delete_files(backup_dir: Path, names: list[str]) -> list[str]:
    """Delete these names from the backup folder. Refuses (raises) any name that is not one of the tool's own."""
    for name in names:
        if not core.is_own_file(name):
            raise BackupError(f"refusing to delete {name!r}: it is not a file this tool made")
    gone = []
    for name in names:
        (backup_dir / name).unlink(missing_ok=True)
        gone.append(name)
    return gone
