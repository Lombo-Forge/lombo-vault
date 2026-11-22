"""Command line. Talks to the disk, the age program and the network; core.py decides.

lombo-vault doctor   check the settings, the age program, the folders and (if set) the key; exit 1 on any failure
lombo-vault plan     show what a backup would contain and what retention would delete; changes nothing
lombo-vault backup   make one encrypted backup; then apply retention (deletes only when DRY_RUN=false)
lombo-vault drill    restore the newest backup into a scratch folder and check it against its manifest
lombo-vault list     show the backups in the folder
lombo-vault prune    apply retention on its own (deletes only when DRY_RUN=false)
"""

import argparse
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime

from . import __version__, agebin, command, core, notify, status, store
from .config import PREFIX, SETTINGS, Config, ConfigError, from_env
from .drill import run_drill

Clock = Callable[[], datetime]


def _clock() -> datetime:
    return datetime.now(UTC)


def _alert(cfg: Config, text: str) -> None:
    if cfg.webhook_url:
        try:
            notify.send(cfg.webhook_url, f"LomboVault: {text}")
        except notify.NotifyError as e:
            print(f"[warn] {e}", file=sys.stderr)


def _ping(url: str | None, failed: bool) -> None:
    if url:
        try:
            notify.ping(url, failed)
        except notify.NotifyError as e:
            print(f"[warn] {e}", file=sys.stderr)


def _record(cfg: Config, now: datetime, section: str, entry: dict[str, object], ok: bool) -> None:
    """Update the status file, if one is set. A problem here is a warning, never a failure of the job."""
    if cfg.status_file is None:
        return
    try:
        status.record(cfg.status_file, section, entry, ok, now.timestamp())
    except OSError as e:
        print(f"[warn] could not write the status file: {e.strerror or e}", file=sys.stderr)


def _mb(n: int) -> str:
    return f"{n / 2**20:.1f} MB"


def doctor(cfg: Config, now: datetime) -> int:
    failed = False

    def report(level: str, label: str, detail: str) -> None:
        nonlocal failed
        failed = failed or level == "FAIL"
        print(f"[{level:^4}] {label}: {detail}")

    try:
        report("ok", "age program", agebin.version(cfg.age_bin))
    except agebin.AgeError as e:
        report("FAIL", "age program", str(e))
        return 1  # nothing below works without it

    try:
        with tempfile.TemporaryFile() as out, agebin.encryptor(cfg.age_bin, cfg.recipients, out) as sink:
            sink.write(b"lombo-vault doctor")
        report("ok", "recipients", f"age accepted {len(cfg.recipients)} public key(s); backups can be encrypted")
    except agebin.AgeError as e:
        report("FAIL", "recipients", str(e))

    for problem in store.check_layout(cfg):
        report("FAIL", "folders", problem)
    for src in cfg.sources:
        if src.exists() and not os.access(src, os.R_OK):
            report("FAIL", "sources", f"{src} is not readable by this user")
    readable = [p for p in cfg.sources if p.exists() and os.access(p, os.R_OK)]
    if readable:
        report("ok", "sources", f"{len(readable)} readable of {len(cfg.sources)}")

    probe = cfg.backup_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if os.access(probe, os.W_OK):
        free = os.statvfs(probe)
        report("ok", "backup folder", f"{cfg.backup_dir} (writable, {_mb(free.f_bavail * free.f_frsize)} free)")
    else:
        report("FAIL", "backup folder", f"{cfg.backup_dir} is not writable by this user")

    if cfg.key_file is None:
        report("warn", "key file", "not set: `drill` will only be PARTIAL (it cannot decrypt). See docs/security.md")
    elif not cfg.key_file.is_file() or not os.access(cfg.key_file, os.R_OK):
        report("FAIL", "key file", f"{cfg.key_file} is missing or not readable")
    else:
        key = cfg.key_file.resolve()
        if core.overlaps(key, cfg.backup_dir.resolve()):
            report("FAIL", "key file", "the key is inside the backup folder: anyone who gets the backups gets the key")
        elif stat.S_IMODE(key.stat().st_mode) & 0o077:
            report("warn", "key file", "other users can read the key file; run chmod 600 on it")
        try:
            agebin.round_trip(cfg.age_bin, cfg.recipients, cfg.key_file)
            report("ok", "key file", "opens a test message made for your recipients")
        except agebin.AgeError as e:
            report("FAIL", "key file", str(e))

    complete = core.complete_stamps(list(store.list_folder(cfg.backup_dir)))
    if complete:
        age = core.age_days(complete[0], now)
        report("ok" if age <= cfg.max_age_days else "warn", "newest backup", f"{complete[0]} ({age:.1f} days old)")
    else:
        report("warn", "newest backup", "none yet")
    for label, argv in (
        ("pre-backup command", cfg.pre_backup_command),
        ("post-backup command", cfg.post_backup_command),
    ):
        if argv:
            if shutil.which(argv[0]):
                report("ok", label, f"{argv[0]} found (run without a shell, {cfg.command_timeout} s limit)")
            else:
                report("FAIL", label, f"{argv[0]} is not found or not executable")
    if cfg.status_file is not None:
        place = cfg.status_file.parent
        while not place.exists() and place != place.parent:
            place = place.parent
        if os.access(place, os.W_OK):
            report("ok", "status file", f"{cfg.status_file} (written after each backup and drill)")
        else:
            report("FAIL", "status file", f"{cfg.status_file} cannot be written by this user")
    report(
        "ok",
        "alerts",
        f"webhook {'set' if cfg.webhook_url else 'not set'}; ping {'set' if cfg.ping_url else 'not set'}",
    )
    report(
        "ok",
        "mode",
        "old backups are only listed, never deleted (dry run)" if cfg.dry_run else "LIVE: old backups are deleted",
    )
    return 1 if failed else 0


def _prune_plan(cfg: Config, now: datetime) -> list[str]:
    return core.plan_prune(store.list_folder(cfg.backup_dir), cfg.keep, now.timestamp())


def _apply_prune(cfg: Config, now: datetime) -> int:
    doomed = _prune_plan(cfg, now)
    if not doomed:
        print(f"retention: nothing to delete (keeping the newest {cfg.keep} complete backups)")
        return 0
    if cfg.dry_run:
        print(f"retention (dry run, set LOMBO_VAULT_DRY_RUN=false to delete): would delete {len(doomed)} file(s):")
        for name in doomed:
            print(f"  {name}")
        return 0
    try:
        gone = store.delete_files(cfg.backup_dir, doomed)
    except (store.BackupError, OSError) as e:
        print(f"[FAIL] retention: {e}", file=sys.stderr)
        return 1
    print(f"retention: deleted {len(gone)} file(s), kept the newest {cfg.keep} complete backups")
    return 0


def plan(cfg: Config, now: datetime) -> int:
    try:
        problems = store.check_layout(cfg)
        if problems:
            print("[FAIL] " + "; ".join(problems), file=sys.stderr)
            return 1
        found = store.scan(cfg)
    except store.BackupError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    print(f"A backup now would be {core.archive_name(core.stamp_of(now))} in {cfg.backup_dir}")
    print(f"  sources: {', '.join(str(p) for p in cfg.sources)}")
    print(f"  {found.files} files, {_mb(found.bytes)} before compression, for {len(cfg.recipients)} recipient(s)")
    for line in found.skipped[:10]:
        print(f"  skipped: {line}")
    if len(found.skipped) > 10:
        print(f"  ... and {len(found.skipped) - 10} more skipped")
    return _apply_prune(cfg, now)


def backup(cfg: Config, now: datetime) -> int:
    done = None
    problem = None
    pre = command.run(cfg.pre_backup_command, cfg.command_timeout)
    if pre is not None:
        problem = f"{PREFIX}PRE_BACKUP_COMMAND failed ({pre}), so no backup was made"
    else:
        try:
            done = store.write_backup(cfg, now)
        except store.BackupError as e:
            problem = str(e)
    # The post command always runs when one is set: it is where a stopped app is started again.
    post = command.run(cfg.post_backup_command, cfg.command_timeout)
    post_text = f"{PREFIX}POST_BACKUP_COMMAND failed ({post})" if post is not None else None
    if done is None:
        text = f"{problem}; {post_text}" if post_text else str(problem)
        print(f"[FAIL] backup: {text}", file=sys.stderr)
        print("Older backups were not touched.", file=sys.stderr)
        _alert(cfg, f"BACKUP FAILED: {text}. Older backups were kept.")
        _ping(cfg.ping_url, True)
        _record(cfg, now, "backup", {"result": "fail", "stamp": None, "files": None, "bytes": None,
                                     "archive_bytes": None, "message": text}, False)  # fmt: skip
        return 1
    print(f"backup {done.stamp}: {done.files} files ({_mb(done.bytes)}) -> {done.archive.name}")
    print(f"  {_mb(done.archive_size)} after compression and encryption")
    entry = {"result": "ok", "stamp": done.stamp, "files": done.files, "bytes": done.bytes,
             "archive_bytes": done.archive_size, "message": f"backup {done.stamp} written"}  # fmt: skip
    if post_text:
        print(f"[FAIL] {post_text}: the backup itself was written", file=sys.stderr)
        _alert(cfg, f"Backup {done.stamp} was written, but {post_text}. An app may still be stopped.")
        entry |= {"result": "fail", "message": f"backup {done.stamp} written, but {post_text}"}
    _ping(cfg.ping_url, post_text is not None)
    _record(cfg, now, "backup", entry, True)
    return _apply_prune(cfg, now) or (1 if post_text else 0)


def drill(cfg: Config, now: datetime) -> int:
    try:
        verdict = run_drill(cfg, now)
    except Exception as e:  # noqa: BLE001  # a drill that dies silently is the worst outcome: report it as a failure
        verdict = core.Verdict("fail", f"the drill stopped unexpectedly ({type(e).__name__}: {e})")
    print(f"restore drill: {verdict.result.upper()}: {verdict.message}")
    heading = {"ok": "Restore drill passed", "partial": "Restore drill PARTIAL (nothing was decrypted)"}
    _alert(cfg, f"{heading.get(verdict.result, 'RESTORE DRILL FAILED')}: {verdict.message}")
    # Only a real pass pings success: PARTIAL (nothing decrypted) must not keep a monitor green.
    _ping(cfg.drill_ping_url, verdict.result != "ok")
    _record(cfg, now, "drill", {"result": verdict.result, "message": verdict.message}, verdict.result == "ok")
    return 1 if verdict.result == "fail" else 0


def list_backups(cfg: Config, now: datetime) -> int:
    files = store.list_folder(cfg.backup_dir)
    found = core.runs(list(files))
    if not found:
        print(f"No backups in {cfg.backup_dir}")
        return 0
    for stamp in sorted(found, reverse=True):
        kinds = found[stamp]
        status = "complete" if kinds == {"archive", "manifest"} else "INCOMPLETE (retention keeps it until it is old)"
        size = _mb(int(os.stat(cfg.backup_dir / core.archive_name(stamp)).st_size)) if "archive" in kinds else "-"
        print(f"{stamp}  {core.age_days(stamp, now):6.1f} days  {size:>10}  {status}")
    return 0


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, clock: Clock = _clock) -> int:
    p = argparse.ArgumentParser(prog="lombo-vault", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("doctor", "plan", "backup", "drill", "list", "prune"):
        sub.add_parser(name)
    args = p.parse_args(argv)

    try:
        cfg = from_env(dict(os.environ) if env is None else env)
    except ConfigError as e:
        print(f"[FAIL] settings: {e}", file=sys.stderr)
        return 2

    source = dict(os.environ) if env is None else env
    for key in sorted(source):
        if key.startswith(PREFIX) and key[len(PREFIX) :] not in SETTINGS:
            print(f"[warn] {key} is not a setting of this tool and is ignored (typo?)", file=sys.stderr)

    commands: dict[str, Callable[[Config, datetime], int]] = {
        "doctor": doctor,
        "plan": plan,
        "backup": backup,
        "drill": drill,
        "list": list_backups,
        "prune": lambda c, n: _apply_prune(c, n),
    }
    return commands[args.cmd](cfg, clock())


if __name__ == "__main__":
    sys.exit(main())
