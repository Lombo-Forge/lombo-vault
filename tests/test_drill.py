"""The restore drill: every way it must pass, say partial, or fail."""

import hashlib
import shutil
import types

import pytest

from conftest import at, key_text, recipient
from lombo_vault import core, drill
from lombo_vault.cli import main
from lombo_vault.config import from_env

HEAD = b"age-encryption.org/v1\n"


@pytest.fixture
def made(env, key_file, tmp_path):
    """A finished backup at at(1) and the settings for a drill that has the key."""
    assert main(["backup"], env, clock=lambda: at(1)) == 0
    cfg = {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}
    folder = tmp_path / "backups"
    stamp = core.stamp_of(at(1))
    return cfg, folder, stamp


def rewrite_manifest(folder, stamp, files=None):
    """Re-write the manifest so it matches the archive on disk (the test altered the archive)."""
    old = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
    data = (folder / core.archive_name(stamp)).read_bytes()
    listed = files if files is not None else {n: (h, 1) for n, h in old.files.items()}
    text = core.build_manifest(stamp, hashlib.sha256(data).hexdigest(), len(data), listed, ["/x"])
    (folder / core.manifest_name(stamp)).write_text(text)


def run(cfg, capsys, days=0):
    code = main(["drill"], cfg, clock=lambda: at(days))
    return code, capsys.readouterr().out


def test_a_good_backup_passes_and_the_scratch_folder_is_removed(made, tmp_path, capsys, hooks):
    cfg, _folder, stamp = made
    cfg = {**cfg, "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h", "LOMBO_VAULT_DRILL_PING_URL": hooks.url + "/d"}
    code, out = run(cfg, capsys)
    assert code == 0
    assert "restore drill: OK" in out
    assert stamp in out
    assert "all 3 files" in out
    assert list((tmp_path / "scratch").iterdir()) == []
    assert hooks.pings() == ["/d"]
    assert "Restore drill passed" in hooks.posts()[0]["body"]["content"]


def test_without_a_key_the_result_is_partial_and_says_so(made, capsys, hooks):
    cfg, _folder, _stamp = made
    cfg = {k: v for k, v in cfg.items() if k != "LOMBO_VAULT_KEY_FILE"}
    cfg["LOMBO_VAULT_WEBHOOK_URL"] = hooks.url + "/h"
    code, out = run(cfg, capsys)
    assert code == 0
    assert "PARTIAL" in out
    assert "NOT decrypted" in out
    assert "PARTIAL" in hooks.posts()[0]["body"]["content"]


def test_a_flipped_byte_in_the_archive_fails(made, capsys, hooks):
    cfg, folder, stamp = made
    archive = folder / core.archive_name(stamp)
    data = bytearray(archive.read_bytes())
    data[-5] ^= 0xFF
    archive.write_bytes(bytes(data))
    code, out = run({**cfg, "LOMBO_VAULT_DRILL_PING_URL": hooks.url + "/d"}, capsys)
    assert code == 1
    assert "does not match its manifest" in out
    assert hooks.pings() == ["/d/fail"]


def test_a_changed_manifest_fails(made, capsys):
    cfg, folder, stamp = made
    path = folder / core.manifest_name(stamp)
    path.write_text(path.read_text().replace('"archive_size": ', '"archive_size": 1'))
    code, out = run(cfg, capsys)
    assert code == 1
    assert "does not match its manifest" in out


@pytest.mark.parametrize("damage", ["garbage", "missing"])
def test_a_damaged_or_missing_manifest_fails(made, capsys, damage):
    cfg, folder, stamp = made
    path = folder / core.manifest_name(stamp)
    if damage == "garbage":
        path.write_text("{")
    else:
        path.unlink()
    code, out = run(cfg, capsys)
    assert code == 1
    assert "FAIL" in out


def test_a_plain_file_is_not_accepted_as_a_backup(made, capsys):
    cfg, folder, stamp = made
    (folder / core.archive_name(stamp)).write_bytes(b"just plain text")
    rewrite_manifest(folder, stamp)
    code, out = run(cfg, capsys)
    assert code == 1
    assert "not an age file" in out


def test_the_wrong_key_fails_with_ages_own_words(made, tmp_path, capsys):
    cfg, _folder, _stamp = made
    other = tmp_path / "other.txt"
    other.write_text(key_text("p"))
    code, out = run({**cfg, "LOMBO_VAULT_KEY_FILE": str(other)}, capsys)
    assert code == 1
    assert "no identity matched" in out
    assert list((tmp_path / "scratch").iterdir()) == []


def test_a_missing_key_file_fails(made, tmp_path, capsys):
    cfg, _folder, _stamp = made
    code, out = run({**cfg, "LOMBO_VAULT_KEY_FILE": str(tmp_path / "gone.txt")}, capsys)
    assert code == 1
    assert "missing or not readable" in out


def test_an_old_backup_fails(made, capsys):
    cfg, _folder, stamp = made
    code, out = run(cfg, capsys, days=-30)
    assert code == 1
    assert "backups have stopped" in out
    assert stamp in out


def test_no_folder_and_no_backup_fail(env, key_file, tmp_path, capsys):
    cfg = {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}
    code, out = run(cfg, capsys)
    assert code == 1
    assert "no backup has ever run" in out
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / core.archive_name(core.stamp_of(at()))).write_text("x")
    code, out = run(cfg, capsys)
    assert code == 1
    assert "no complete backup" in out
    assert "crashed" in out
    (tmp_path / "backups" / core.archive_name(core.stamp_of(at()))).unlink()
    code, out = run(cfg, capsys)
    assert code == 1
    assert "no complete backup" in out
    assert "crashed" not in out


def test_the_drill_checks_the_newest_complete_backup(made, env, capsys):
    cfg, _folder, _stamp = made
    assert main(["backup"], env, clock=lambda: at(0.5)) == 0
    code, out = run(cfg, capsys)
    assert code == 0
    assert core.stamp_of(at(0.5)) in out


def test_files_that_differ_from_the_manifest_fail(made, capsys):
    cfg, folder, stamp = made
    old = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
    names = sorted(old.files)
    files = {n: (old.files[n], 1) for n in names[1:]}
    files[names[0]] = ("f" * 64, 1)  # a checksum that does not match the restored file
    files["srv/never/restored.txt"] = ("e" * 64, 1)
    rewrite_manifest(folder, stamp, files)
    code, out = run(cfg, capsys)
    assert code == 1
    assert "files differ from the manifest" in out
    assert "1 missing" in out
    assert "1 changed" in out


def test_extra_files_in_the_archive_fail(made, capsys):
    cfg, folder, stamp = made
    old = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
    names = sorted(old.files)
    rewrite_manifest(folder, stamp, {n: (old.files[n], 1) for n in names[1:]})
    code, out = run(cfg, capsys)
    assert code == 1
    assert "1 extra" in out


def test_a_manifest_with_no_files_fails(made, capsys):
    cfg, folder, stamp = made
    rewrite_manifest(folder, stamp, {})
    code, out = run(cfg, capsys)
    assert code == 1
    assert "lists no files" in out


def test_a_truncated_archive_fails_even_though_the_manifest_was_rewritten(made, capsys):
    """age checks the whole file, so a cut-off archive must fail at decryption (not just by checksum)."""
    cfg, folder, stamp = made
    archive = folder / core.archive_name(stamp)
    head, _, body = archive.read_bytes()[len(HEAD) :].partition(b"\n")
    archive.write_bytes(HEAD + head + b"\n" + body[:-40])
    rewrite_manifest(folder, stamp)
    code, out = run(cfg, capsys)
    assert code == 1
    assert "could not be restored" in out


def test_garbage_inside_a_valid_age_file_fails(made, capsys):
    cfg, folder, stamp = made
    archive = folder / core.archive_name(stamp)
    payload = bytes(b ^ 0x5A for b in b"this is not a tar archive" + b"AGE-FAKE-END")
    archive.write_bytes(HEAD + recipient("q").encode() + b"\n" + payload)
    rewrite_manifest(folder, stamp)
    code, out = run(cfg, capsys)
    assert code == 1
    assert "could not be restored" in out


def test_a_path_that_escapes_the_scratch_folder_is_refused(made, tmp_path, capsys):
    import gzip
    import io
    import tarfile

    cfg, folder, stamp = made
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("../escaped.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    payload = bytes(b ^ 0x5A for b in gzip.compress(buf.getvalue()) + b"AGE-FAKE-END")
    (folder / core.archive_name(stamp)).write_bytes(HEAD + recipient("q").encode() + b"\n" + payload)
    rewrite_manifest(folder, stamp)
    code, out = run(cfg, capsys)
    assert code == 1
    assert "could not be restored" in out
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "scratch" / "escaped.txt").exists()


def test_not_enough_room_in_the_scratch_folder_fails(made, monkeypatch, capsys):
    cfg, _folder, _stamp = made
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1))
    code, out = run(cfg, capsys)
    assert code == 1
    assert "not enough room" in out
    assert "SCRATCH_DIR" in out


def test_a_missing_scratch_folder_fails(made, tmp_path, capsys):
    cfg, _folder, _stamp = made
    code, out = run({**cfg, "LOMBO_VAULT_SCRATCH_DIR": str(tmp_path / "nope")}, capsys)
    assert code == 1
    assert "scratch folder" in out


def test_the_default_scratch_folder_is_the_system_temp(made, tmp_path, monkeypatch, capsys):
    cfg, _folder, _stamp = made
    cfg = {k: v for k, v in cfg.items() if k != "LOMBO_VAULT_SCRATCH_DIR"}
    monkeypatch.setenv("TMPDIR", str(tmp_path / "scratch"))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", None)
    code, out = run(cfg, capsys)
    assert code == 0
    assert "OK" in out


def test_files_saved_with_mode_000_can_still_be_checked_after_restore():
    import tarfile

    for kind, expected in ((tarfile.REGTYPE, 0o600), (tarfile.DIRTYPE, 0o700)):
        info = tarfile.TarInfo("srv/locked")
        info.type, info.mode = kind, 0
        assert drill._scratch_filter(info, "/scratch").mode == expected


def test_an_unexpected_error_is_reported_as_a_failed_drill(made, monkeypatch, capsys, hooks):
    cfg, _folder, _stamp = made

    def boom(*a):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("lombo_vault.cli.run_drill", boom)
    code, out = run({**cfg, "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h"}, capsys)
    assert code == 1
    assert "stopped unexpectedly" in out
    assert "kaboom" in out
    assert "RESTORE DRILL FAILED" in hooks.posts()[0]["body"]["content"]


def test_hash_tree_ignores_links_and_hashes_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("hello")
    (tmp_path / "a" / "l").symlink_to(tmp_path / "a" / "f.txt")
    assert drill.hash_tree(tmp_path) == {"a/f.txt": hashlib.sha256(b"hello").hexdigest()}


def test_the_drill_only_reads_the_backup_folder(made, capsys):
    cfg, folder, _stamp = made
    before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in folder.iterdir()}
    assert run(cfg, capsys)[0] == 0
    assert {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in folder.iterdir()} == before
    assert from_env(cfg).key_file is not None
