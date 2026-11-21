"""The restore drill: prove the newest backup restores. Never touches the sources or the backup folder
(it only reads the latter); everything is unpacked into a scratch folder that is removed afterwards.
"""

import hashlib
import os
import shutil
import tarfile
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

from . import agebin, core
from .config import Config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


SLACK_BYTES = 1 << 20  # room over the manifest's total for headers and rounding


def _scratch_filter(member: tarfile.TarInfo, dest: str, budget: list[int] | None = None) -> tarfile.TarInfo:
    """tarfile's own tar_filter (refuses paths that leave the folder), plus owner access, so that a file
    saved with mode 000 can still be read back and checked by a non-root user.

    Also refuses members that are not a file, folder or link (a FIFO, a device), and stops once more bytes
    are announced than the manifest says the backup holds: the manifest can be written by anyone who can
    write to the backup folder, so a size it understates must not fill the disk before the drill notices."""
    if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
        raise tarfile.TarError(f"{member.name!r} is not a file, folder or link")
    if budget is not None:
        budget[0] -= member.size
        if budget[0] < 0:
            raise tarfile.TarError("the archive holds more data than its manifest says")
    safe = tarfile.tar_filter(member, dest)
    extra = 0o700 if safe.isdir() else 0o600
    return safe.replace(mode=(safe.mode or 0) | extra, deep=False)


def hash_tree(root: Path) -> dict[str, str]:
    """{path relative to root: sha256} for every regular file below root (links are not followed)."""
    found: dict[str, str] = {}
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                continue
            found[path.relative_to(root).as_posix()] = _sha256(path)
    return found


def run_drill(cfg: Config, now: datetime) -> core.Verdict:
    folder = cfg.backup_dir
    if not folder.is_dir():
        return core.Verdict("fail", f"the backup folder {folder} does not exist: no backup has ever run")
    names = list(core.runs(sorted(os.listdir(folder))))
    complete = core.complete_stamps(os.listdir(folder))
    if not complete:
        extra = " (there are unfinished files; the last backup probably crashed)" if names else ""
        return core.Verdict("fail", f"no complete backup in {folder}{extra}. Run `lombo-vault backup`")
    stamp = complete[0]
    age = core.age_days(stamp, now)
    if age < -core.MAX_FUTURE_SECONDS / 86400:
        return core.Verdict(
            "fail",
            f"the newest backup ({stamp}) is dated {-age:.0f} days in the future: the clock was wrong when it was "
            "made, or is wrong now. Fix the clock; backups that cannot be trusted by date may be hiding a stop",
        )
    if age > cfg.max_age_days:
        return core.Verdict(
            "fail",
            f"the newest backup ({stamp}) is {age:.0f} days old (limit {cfg.max_age_days}): backups have stopped",
        )

    archive = folder / core.archive_name(stamp)
    try:
        manifest = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
        size = archive.stat().st_size
        with archive.open("rb") as handle:
            header = handle.read(len(core.AGE_HEADER))
        digest = _sha256(archive)
    except (OSError, UnicodeDecodeError, core.ManifestError) as e:
        return core.Verdict("fail", f"backup {stamp} cannot be read: {e}")
    if size != manifest.archive_size or digest != manifest.archive_sha256:
        return core.Verdict("fail", f"backup {stamp} does not match its manifest (the archive was damaged or changed)")
    if header != core.AGE_HEADER:
        return core.Verdict("fail", f"backup {stamp} is not an age file (not encrypted?)")
    if not manifest.files:
        return core.Verdict("fail", f"backup {stamp} lists no files")

    if cfg.key_file is None:
        return core.Verdict(
            "partial",
            f"backup {stamp} ({age:.1f} days old, {len(manifest.files)} files) is intact and encrypted, but it was NOT "
            "decrypted: no KEY_FILE is set. Set LOMBO_VAULT_KEY_FILE on the machine that runs the drill",
        )
    if not cfg.key_file.is_file() or not os.access(cfg.key_file, os.R_OK):
        return core.Verdict("fail", f"the key file {cfg.key_file} is missing or not readable")

    base = cfg.scratch_dir or Path(tempfile.gettempdir())
    if not base.is_dir():
        return core.Verdict("fail", f"the scratch folder {base} does not exist")
    need = core.space_needed(manifest.total_bytes)
    free = shutil.disk_usage(base).free
    if free < need:
        return core.Verdict(
            "fail",
            f"not enough room in {base} for the drill ({free // 2**20} MB free, {need // 2**20} MB needed); "
            "set LOMBO_VAULT_SCRATCH_DIR to a bigger disk",
        )

    scratch = Path(tempfile.mkdtemp(prefix="lombovault-drill-", dir=base))
    try:
        try:
            with (
                archive.open("rb") as source,
                agebin.decryptor(cfg.age_bin, cfg.key_file, source) as plain,
                tarfile.open(fileobj=plain, mode="r|gz") as tar,
            ):
                budget = [manifest.total_bytes + SLACK_BYTES]
                tar.extractall(  # noqa: S202  # filter refuses paths leaving the folder
                    scratch, filter=lambda m, d: _scratch_filter(m, d, budget)
                )
            restored = hash_tree(scratch)
        except (agebin.AgeError, tarfile.TarError, OSError, EOFError, zlib.error) as e:
            return core.Verdict("fail", f"backup {stamp} could not be restored: {e}")
        diff = core.compare_manifest(manifest.files, restored)
        if not diff.clean:
            return core.Verdict(
                "fail", f"backup {stamp} restored, but the files differ from the manifest: {diff.summary()}"
            )
        return core.Verdict(
            "ok",
            f"backup {stamp} ({age:.1f} days old) was decrypted and unpacked; all {len(restored)} files "
            f"({manifest.total_bytes} bytes) match the checksums written at backup time",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
